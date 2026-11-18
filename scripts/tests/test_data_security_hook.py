"""Hợp đồng chặn cùng lỗi mới qua bốn dạng payload ghi."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1]))
from lib import write_change, security_check

FILE = 'srcroot/boundaries/d-profile/src/features/x/x.service.ts'
FINDING = {'fingerprint': 'body', 'rule': 'SEC003', 'line': 1, 'message': 'log body'}


class SecurityHookTest(unittest.TestCase):
    def invoke(self, payload, inspect):
        with patch.object(security_check, 'inspect_source', side_effect=inspect), contextlib.redirect_stderr(io.StringIO()):
            return security_check.check({'tool_input': payload})

    def test_four_payloads_block_same_new_error(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(write_change, 'ROOT', Path(tmp)):
            path = Path(tmp)/FILE
            path.parent.mkdir(parents=True)
            path.write_text('old\n')
            payloads = [
                {'file_path': str(path), 'content': 'new\n'},
                {'file_path': str(path), 'old_string': 'old', 'new_string': 'new'},
                {'file_path': str(path), 'edits': [{'old_string': 'old', 'new_string': 'new'}]},
                {'command': f'*** Begin Patch\n*** Update File: {FILE}\n@@\n-old\n+new\n*** End Patch'},
            ]
            for payload in payloads:
                self.assertEqual(self.invoke(payload, lambda p,s: [FINDING] if 'new' in s else []), 2)

    def test_existing_error_allowed_but_duplicate_blocked(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(write_change, 'ROOT', Path(tmp)):
            path = Path(tmp)/FILE
            path.parent.mkdir(parents=True)
            path.write_text('old')
            value = {'file_path': str(path), 'content': 'new'}
            self.assertEqual(self.invoke(value, lambda *_: [FINDING]), 0)
            self.assertEqual(self.invoke(value, lambda p,s: [FINDING]*(2 if s=='new' else 1)), 2)

    def test_unavailable_checker_blocks(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(write_change, 'ROOT', Path(tmp)):
            self.assertEqual(self.invoke({'file_path': str(Path(tmp)/FILE), 'content': 'new'}, ValueError()), 2)

    def test_unrelated_files_ignored(self):
        with patch.object(security_check, 'inspect_source') as inspect:
            self.assertEqual(security_check.check({'tool_input': {'file_path': 'scripts/a.py', 'content': 'x'}}), 0)
            inspect.assert_not_called()

    def test_DS_T01_rename_does_not_hide_unsafe_added_file(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(write_change, 'ROOT', Path(tmp)):
            path = Path(tmp)/FILE
            path.parent.mkdir(parents=True)
            path.write_text('old\n')
            patch_text = (f'*** Begin Patch\n*** Update File: {FILE}\n'
                          f'*** Move to: {FILE.replace("x.service", "y.service")}\n@@\n-old\n+new\n'
                          '*** Add File: srcroot/boundaries/d-profile/src/unsafe.ts\n'
                          '+prisma.$queryRawUnsafe(userInput)\n*** End Patch')
            self.assertEqual(self.invoke({'command': patch_text}, lambda p,s: [FINDING] if '$queryRawUnsafe' in s else []), 2)

    def test_unparseable_boundary_patch_cannot_skip_security_check(self):
        text=f'*** Begin Patch\n*** Update File: {FILE}\n@@\n-does-not-exist\n+unsafe\n*** End Patch'
        with tempfile.TemporaryDirectory() as tmp, patch.object(write_change,'ROOT',Path(tmp)):
            self.assertEqual(self.invoke({'command':text},lambda *_:[]),2)


if __name__ == '__main__':
    unittest.main()
