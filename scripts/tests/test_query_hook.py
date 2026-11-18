"""Hai kiểu payload phải cảnh báo cùng truy vấn mới, không chặn sửa code."""
from collections import Counter
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1]))
from lib import query_check, write_change

FILE = 'srcroot/boundaries/d-profile/src/features/x/x.repository.ts'
FINDING = {'rule': 'QS001', 'fingerprint': 'old', 'line': 1, 'message': 'thiếu take'}


class QueryHookTest(unittest.TestCase):
    def invoke(self, value, inspect):
        with patch.object(query_check, 'inspect_source', side_effect=inspect), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            result = query_check.check({'tool_input': value})
        self.assertEqual(result, 0)
        return json.loads(out.getvalue())['hookSpecificOutput']['additionalContext'] if out.getvalue() else ''

    def test_write_edit_multi_edit_patch_match(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(write_change, 'ROOT', Path(tmp)):
            path = Path(tmp) / FILE
            path.parent.mkdir(parents=True); path.write_text('old\n')
            payloads = [
                {'file_path': str(path), 'content': 'new\n'},
                {'file_path': str(path), 'old_string': 'old', 'new_string': 'new'},
                {'file_path': str(path), 'edits': [{'old_string': 'old', 'new_string': 'new'}]},
                {'command': f'*** Begin Patch\n*** Update File: {FILE}\n@@\n-old\n+new\n*** End Patch'},
            ]
            outputs = [self.invoke(p, lambda _p, s: [FINDING] if 'new' in s else []) for p in payloads]
            self.assertTrue(outputs[0]); self.assertEqual(len(set(outputs)), 1)

    def test_existing_violation_and_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(write_change, 'ROOT', Path(tmp)):
            path = Path(tmp) / FILE
            path.parent.mkdir(parents=True); path.write_text('old')
            value = {'file_path': str(path), 'content': 'new'}
            self.assertEqual(self.invoke(value, lambda *_: [FINDING]), '')
            self.assertIn('QS001', self.invoke(value, lambda _p, s: [FINDING] * (2 if s == 'new' else 1)))

    def test_unavailable_checker_says_not_checked(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(write_change, 'ROOT', Path(tmp)):
            value = {'file_path': str(Path(tmp) / FILE), 'content': 'new'}
            self.assertIn('CHƯA KIỂM ĐƯỢC', self.invoke(value, ValueError('thiếu dependency')))

    def test_other_file_does_not_run_checker(self):
        with patch.object(query_check, 'inspect_source') as inspect:
            query_check.check({'tool_input': {'file_path': 'scripts/a.py', 'content': 'x'}})
            inspect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
