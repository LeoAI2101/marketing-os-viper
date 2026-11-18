"""Chạy audit thật trên repo tạm: hook phải nhìn cả schema và registry sau patch."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from lib import write_change, lifecycle_check
from test_data_lifecycle import MODEL, SCHEMA, registry, row

REGISTRY = 'context/DATA-LIFECYCLE.md'


class DataLifecycleHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root_patch = patch.object(write_change, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.write(SCHEMA, MODEL)
        self.write(REGISTRY, registry(row()))

    def write(self, file, text):
        path = self.root / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def invoke(self, value):
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            return lifecycle_check.check({'tool_input': value})

    def test_new_model_without_policy_blocked_in_all_payload_shapes(self):
        addition = MODEL.replace('Record', 'NewRecord')
        path = str(self.root / SCHEMA)
        payloads = [
            {'file_path': path, 'content': MODEL + addition},
            {'file_path': path, 'old_string': MODEL, 'new_string': MODEL + addition},
            {'file_path': path, 'edits': [{'old_string': MODEL, 'new_string': MODEL + addition}]},
            {'command': f'*** Begin Patch\n*** Update File: {SCHEMA}\n@@\n' +
             ''.join(' ' + line + '\n' for line in MODEL.splitlines()) +
             ''.join('+' + line + '\n' for line in addition.splitlines()) + '*** End Patch'},
        ]
        for payload in payloads:
            with self.subTest(keys=list(payload)):
                self.assertEqual(self.invoke(payload), 2)

    def test_schema_and_policy_in_same_patch_evaluated_together(self):
        new_file = 'srcroot/boundaries/d-booking/prisma/schema.prisma'
        patch_text = f'*** Begin Patch\n*** Add File: {new_file}\n'
        patch_text += ''.join('+' + line + '\n' for line in MODEL.splitlines())
        patch_text += f'*** Update File: {REGISTRY}\n@@\n {row()}\n+{row(target="d-booking")}\n'
        patch_text += ' <!-- DATA-LIFECYCLE:END -->\n*** End Patch'
        self.assertEqual(self.invoke({'command': patch_text}), 0)

    def test_removing_registry_row_of_live_model_is_blocked(self):
        self.assertEqual(self.invoke({'file_path': str(self.root / REGISTRY), 'content': registry()}), 2)
        patch_text = (f'*** Begin Patch\n*** Update File: {REGISTRY}\n@@\n'
                      f'-{row()}\n <!-- DATA-LIFECYCLE:END -->\n*** End Patch')
        self.assertEqual(self.invoke({'command': patch_text}), 2)

    def test_old_pending_rows_do_not_block_unrelated_field_edit(self):
        models = [MODEL.replace('Record', f'Record{i}') for i in range(77)]
        source = '\n'.join(models)
        self.write(SCHEMA, source)
        self.write(REGISTRY, registry(*(row(model=f'Record{i}', status='pending') for i in range(77))))
        changed = source.replace('model Record0 {', 'model Record0 {\n  note String?')
        self.assertEqual(self.invoke({'file_path': str(self.root / SCHEMA), 'content': changed}), 0)

    def test_old_pending_does_not_hide_new_missing_or_duplicate(self):
        self.write(REGISTRY, registry(row(status='pending')))
        self.assertEqual(self.invoke({'file_path': str(self.root / SCHEMA),
                                     'content': MODEL + MODEL.replace('Record', 'NewRecord')}), 2)
        self.assertEqual(self.invoke({'file_path': str(self.root / REGISTRY),
                                     'content': registry(row(status='pending'), row(status='pending'))}), 2)

    def test_malformed_registry_does_not_allow_new_model(self):
        self.write(REGISTRY, 'Chưa có bảng hoặc markers')
        self.assertEqual(self.invoke({'file_path': str(self.root / SCHEMA),
                                     'content': MODEL + MODEL.replace('Record', 'NewRecord')}), 2)

    def test_fixing_broken_registry_not_blocked_by_broken_baseline(self):
        self.write(REGISTRY, '<!-- DATA-LIFECYCLE:BEGIN -->\nBảng chưa hoàn thành')
        self.assertEqual(self.invoke({'file_path': str(self.root / REGISTRY),
                                     'content': registry(row())}), 0)

    def test_unrelated_file_not_blocked_by_old_registry_debt(self):
        self.write(REGISTRY, 'Chưa có bảng hoặc markers')
        self.assertEqual(self.invoke({'file_path': str(self.root / 'scripts/example.py'), 'content': 'x = 1\n'}), 0)

    def test_broken_baseline_does_not_waive_findings_of_edit(self):
        self.write(REGISTRY, '<!-- DATA-LIFECYCLE:BEGIN -->\nBảng chưa hoàn thành')
        for proposed in [registry(), registry(row(status='pending')), registry(row(), row()),
                         registry(row().replace('365 ngày', '**unknown**'))]:
            with self.subTest(proposed=proposed):
                self.assertEqual(self.invoke({'file_path': str(self.root / REGISTRY), 'content': proposed}), 2)

    def test_broken_schema_fixed_is_judged_by_new_version(self):
        self.write(SCHEMA, 'model Record {')
        self.assertEqual(self.invoke({'file_path': str(self.root / SCHEMA), 'content': MODEL}), 0)
        self.assertEqual(self.invoke({'file_path': str(self.root / SCHEMA),
                                     'content': MODEL + MODEL.replace('Record', 'NewRecord')}), 2)

    def test_broken_schema_and_registry_in_same_patch_do_not_fake_green(self):
        self.write(SCHEMA, 'model Record {\n')
        self.write(REGISTRY, 'Sổ bị hỏng\n')
        patch_text = f'*** Begin Patch\n*** Update File: {SCHEMA}\n@@\n-model Record {{\n'
        patch_text += ''.join('+' + line + '\n' for line in MODEL.splitlines())
        patch_text += f'*** Update File: {REGISTRY}\n@@\n-Sổ bị hỏng\n'
        patch_text += ''.join('+' + line + '\n' for line in registry(row()).splitlines())
        patch_text += '*** End Patch'
        self.assertEqual(self.invoke({'command': patch_text}), 0)
        self.assertEqual(self.invoke({'command': patch_text.replace('365 ngày', '**unknown**')}), 2)

    def test_hiding_confirmed_policy_in_comment_is_blocked(self):
        proposed = registry('<!--\n' + row() + '\n-->')
        self.assertEqual(self.invoke({'file_path': str(self.root / REGISTRY), 'content': proposed}), 2)


if __name__ == '__main__':
    unittest.main()
