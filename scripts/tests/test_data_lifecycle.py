"""Registry phải gắn đúng model/boundary; chưa chốt chính sách không thể xanh."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from lib.data_lifecycle import audit, parse_models, parse_registry

SCHEMA = 'srcroot/boundaries/d-profile/prisma/schema.prisma'
OTHER = 'srcroot/boundaries/d-booking/prisma/schema.prisma'
MODEL = 'model Record {\n  id String @id\n}\n'
HEADER = ('<!-- DATA-LIFECYCLE:BEGIN -->\n'
          '| Target | Model | Retention | Archive | Purge | Legal hold | Trạng thái | Bằng chứng |\n'
          '| --- | --- | --- | --- | --- | --- | --- | --- |\n')


def row(target='d-profile', model='Record', status='confirmed'):
    return f'| {target} | {model} | 365 ngày | Kho lưu trữ riêng | Xoá sau hết hạn | Hoãn xoá khi có yêu cầu | {status} | DECISIONS: phê duyệt thử nghiệm |'


def registry(*rows):
    return HEADER + '\n'.join(rows) + '\n<!-- DATA-LIFECYCLE:END -->\n'


def rules(report):
    return [finding['rule'] for finding in report['findings']]


class DataLifecycleTests(unittest.TestCase):
    def test_parser_ignores_fake_models_in_comments_strings_nested_blocks(self):
        source = '''// model FakeA { id String @id }
/* model FakeB { id String @id } */
generator client {
  provider = "model FakeC { x String }"
}
model
Record
{
  id String @id
  text String @default("model FakeD { }")
  @@map("records")
}
enum State { OPEN CLOSED }
'''
        models = parse_models(source)
        self.assertEqual([model['name'] for model in models], ['Record'])
        self.assertEqual(models[0]['line'], 6)
        self.assertEqual(audit({SCHEMA: source}, registry(row()))['errors'], 0)

    def test_same_model_name_in_two_boundaries_needs_two_policies(self):
        schemas = {SCHEMA: MODEL, OTHER: MODEL}
        incomplete = audit(schemas, registry(row()))
        self.assertIn('DL001', rules(incomplete))
        self.assertGreater(incomplete['errors'], 0)
        complete = audit(schemas, registry(row(), row(target='d-booking')))
        self.assertEqual(complete['errors'], 0)
        self.assertEqual(len(complete['models']), 2)

    def test_new_model_does_not_inherit_old_model_policy(self):
        report = audit({SCHEMA: MODEL + MODEL.replace('Record', 'NewRecord')}, registry(row()))
        self.assertEqual(rules(report).count('DL001'), 1)
        self.assertGreater(report['errors'], 0)

    def test_pending_and_empty_cells_are_not_confirmed(self):
        pending = audit({SCHEMA: MODEL}, registry(row(status='pending')))
        self.assertIn('DL002', rules(pending))
        self.assertGreater(pending['errors'], 0)
        for column in range(2, 8):
            cells = row().strip('|').split('|')
            cells[column] = ' '
            with self.subTest(column=column):
                report = audit({SCHEMA: MODEL}, registry('|' + '|'.join(cells) + '|'))
                self.assertIn('DL003', rules(report))
                self.assertGreater(report['errors'], 0)

    def test_duplicate_rows_and_missing_models_make_registry_red(self):
        duplicate = audit({SCHEMA: MODEL}, registry(row(), row()))
        self.assertIn('DL004', rules(duplicate))
        self.assertGreater(duplicate['errors'], 0)
        for extra in [row(model='DeletedRecord'), row(target='d-booking')]:
            with self.subTest(extra=extra):
                unknown = audit({SCHEMA: MODEL}, registry(row(), extra))
                self.assertIn('DL005', rules(unknown))
                self.assertGreater(unknown['errors'], 0)

    def test_confirmed_cannot_use_placeholder_to_dodge_policy(self):
        for placeholder in ['unknown', 'pending', 'chưa', 'n/a']:
            cells = row().strip('|').split('|')
            cells[2] = placeholder
            with self.subTest(placeholder=placeholder):
                report = audit({SCHEMA: MODEL}, registry('|' + '|'.join(cells) + '|'))
                self.assertIn('DL003', rules(report))
        cells = row().strip('|').split('|')
        cells[5] = 'n/a — cache không lưu dữ liệu nghiệp vụ'
        self.assertEqual(audit({SCHEMA: MODEL}, registry('|' + '|'.join(cells) + '|'))['errors'], 0)

    def test_markdown_example_does_not_waive_missing_policy(self):
        example = '```markdown\n' + registry(row()) + '```\n'
        # Marker giả trong code fence không được chọn thay registry thật ở dưới.
        report = audit({SCHEMA: MODEL}, example + registry())
        self.assertIn('DL001', rules(report))
        inside = registry('```markdown\n' + row() + '\n```')
        report = audit({SCHEMA: MODEL}, inside)
        self.assertIn('DL001', rules(report))
        # Fence bốn dấu chỉ đóng bởi >=4 dấu; ba dấu bên trong vẫn là ví dụ.
        nested_example = registry('````markdown\n```\n' + row() + '\n```\n````')
        report = audit({SCHEMA: MODEL}, nested_example)
        self.assertIn('DL001', rules(report))

    def test_row_hidden_in_html_comment_is_not_confirmed_policy(self):
        report = audit({SCHEMA: MODEL}, registry('<!--\n' + row() + '\n-->'))
        self.assertIn('DL001', rules(report))
        self.assertGreater(report['errors'], 0)

    def test_bold_placeholder_does_not_turn_unknown_into_retention(self):
        cells = row().strip('|').split('|')
        cells[2] = ' **unknown** '
        report = audit({SCHEMA: MODEL}, registry('|' + '|'.join(cells) + '|'))
        self.assertIn('DL003', rules(report))
        self.assertGreater(report['errors'], 0)

    def test_broken_input_reports_unchecked_not_empty_green(self):
        for source in [None, 'model Record {', '/* comment chưa đóng',
                       'model Record { id String @default("chưa đóng) }']:
            with self.subTest(source=source), self.assertRaises(ValueError):
                parse_models(source)
        for text in ['', '<!-- DATA-LIFECYCLE:BEGIN -->', registry(row()) + registry(row())]:
            with self.subTest(registry=text), self.assertRaises(ValueError):
                audit({SCHEMA: MODEL}, text)

    def test_markers_in_comments_do_not_replace_registry_and_keep_line_numbers(self):
        hidden = '<!-- ví dụ bị ẩn\nDATA-LIFECYCLE:BEGIN\n' + row() + '\nDATA-LIFECYCLE:END\n-->\n'
        report = audit({SCHEMA: MODEL}, hidden + registry())
        # HTML không có comment lồng: marker giả có delimiter bên trong phải báo
        # chưa đọc được, không nhận làm một vùng policy khác hoặc trả xanh.
        nested = '<!-- ví dụ bị ẩn\n' + registry(row()) + '-->\n'
        with self.assertRaises(ValueError):
            parse_registry(nested + registry())
        self.assertEqual(rules(report), ['DL001'])
        text = registry('<!-- ghi chú -->\n' + row())
        parsed = parse_registry(text)
        self.assertEqual(parsed[0]['line'], 5)
        self.assertEqual(audit({SCHEMA: MODEL}, registry('<!-- một ghi chú -->' + row()))['errors'], 0)
        with self.assertRaises(ValueError):
            parse_registry(registry(row()) + '<!-- comment chưa đóng')

    def test_comment_and_fence_do_not_open_each_other(self):
        for example in [
            '<!--\n```\n' + row() + '\n-->\n',
            '```\n<!-- comment nằm trong code không cần đóng\n' + row() + '\n```',
            '~~~~md\n~~~\n' + row() + '\n~~~\n~~~~',
            '````md\n````không phải dòng đóng\n' + row() + '\n`````',
            '````md\n~~~~\n' + row() + '\n~~~~\n````',
            '    ' + row(),
        ]:
            with self.subTest(example=example):
                self.assertEqual(rules(audit({SCHEMA: MODEL}, registry(example))), ['DL001'])
        visible = '````md\nví dụ\n`````\n' + row()
        self.assertEqual(audit({SCHEMA: MODEL}, registry(visible))['errors'], 0)

    def test_markdown_placeholder_in_any_policy_column_not_confirmed(self):
        for value in ['***unknown***', '_pending_', '`chưa chốt`', '[unknown](#policy)',
                      '<strong>unknown</strong>', '**unknown** — vẫn chưa biết',
                      '**n/a**', '<!-- không có policy -->']:
            for column in [2, 3, 4, 5, 7]:
                cells = row().strip('|').split('|')
                cells[column] = value
                with self.subTest(value=value, column=column):
                    report = audit({SCHEMA: MODEL}, registry('|' + '|'.join(cells) + '|'))
                    self.assertIn('DL003', rules(report))
                    self.assertEqual(report['confirmed'], 0)
        report = audit({SCHEMA: MODEL}, registry(row(), row()))
        self.assertEqual(report['confirmed'], 0)

    def test_escaped_pipe_keeps_columns_and_formatting_hides_nothing(self):
        text = row().replace('365 ngày', '**365 ngày**').replace('Kho lưu trữ riêng', r'Kho A \| Kho B')
        report = audit({SCHEMA: MODEL}, registry(text))
        self.assertEqual(report['errors'], 0)
        self.assertEqual(report['confirmed'], 1)
        # Hai backslash là một backslash literal; pipe tiếp theo vẫn chia cột.
        text = row().replace('Kho lưu trữ riêng', r'Kho A \\| Kho B')
        with self.assertRaises(ValueError):
            parse_registry(registry(text))
        for text in [row().rstrip('|'), '|']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_registry(registry(text))

    def test_markdown_does_not_mangle_model_names_with_underscore(self):
        source = MODEL.replace('Record', 'Record_With_Underscore')
        report = audit({SCHEMA: source}, registry(row(model='`Record_With_Underscore`')))
        self.assertEqual(report['errors'], 0)
        self.assertEqual(report['confirmed'], 1)

    def test_wrong_input_types_raise_value_error(self):
        for value in [None, 1, [], {}, b'model Record {}']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_models(value)
            with self.subTest(registry=value), self.assertRaises(ValueError):
                parse_registry(value)
        for schemas in [None, [], {None: MODEL}, {SCHEMA: None}]:
            with self.subTest(schemas=schemas), self.assertRaises(ValueError):
                audit(schemas, registry(row()))


if __name__ == '__main__':
    unittest.main()
