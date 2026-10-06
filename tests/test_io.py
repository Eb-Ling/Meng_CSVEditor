import codecs
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from csv_model import (
    CsvFormat, CsvTableModel, csv_format_for_save, detect_encoding,
    read_csv, read_csv_document, write_csv,
)


class CsvIoTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'input.csv'

    def roundtrip(self, raw, expected_rows=None, expected_output=None):
        self.path.write_bytes(raw)
        rows, file_format = read_csv_document(self.path)
        if expected_rows is not None:
            self.assertEqual(rows, expected_rows)
        model = CsvTableModel()
        model.load_data(rows)
        write_csv(self.path, model.get_all_data(), csv_format=file_format)
        self.assertEqual(self.path.read_bytes(), raw if expected_output is None else expected_output)
        return file_format

    def test_ragged_rows_blank_records_and_lf_are_retained(self):
        file_format = self.roundtrip(b'a,b\nc\n\n', [['a', 'b'], ['c'], []])
        self.assertEqual(file_format.lineterminator, '\n')

    def test_tsv_is_detected_and_preserved(self):
        self.path = self.path.with_suffix('.tsv')
        self.assertEqual(self.roundtrip(b'a\tb\r\nc\td\r\n').delimiter, '\t')

    def test_semicolon_and_pipe_delimiters_are_retained(self):
        for delimiter in (';', '|'):
            with self.subTest(delimiter=delimiter):
                raw = f'name{delimiter}value\na{delimiter}b\n'.encode()
                self.assertEqual(self.roundtrip(raw).delimiter, delimiter)

    def test_ragged_semicolon_file_without_consistent_width(self):
        self.assertEqual(self.roundtrip(b'a;b;c\nd\ne;f\n').delimiter, ';')

    def test_utf8_bom_is_removed_and_no_final_newline_is_retained(self):
        payload = '名字,数值\n中文,1'.encode()
        file_format = self.roundtrip(codecs.BOM_UTF8 + payload, expected_output=payload)
        self.assertEqual(file_format.encoding, 'utf-8-sig')
        self.assertFalse(file_format.terminal_newline)
        self.assertEqual(read_csv_document(self.path)[1].encoding, 'utf-8')

    def test_utf16_and_utf32_inputs_are_saved_as_utf8_without_bom(self):
        for encoding, bom in (
            ('utf-16-le', codecs.BOM_UTF16_LE), ('utf-16-be', codecs.BOM_UTF16_BE),
            ('utf-32-le', codecs.BOM_UTF32_LE), ('utf-32-be', codecs.BOM_UTF32_BE),
        ):
            with self.subTest(encoding=encoding):
                raw = bom + '名字,数值\r\n中文,1'.encode(encoding)
                expected = '名字,数值\r\n中文,1'.encode('utf-8')
                self.assertEqual(self.roundtrip(raw, expected_output=expected).encoding, encoding)
                self.assertEqual(read_csv_document(self.path)[0], [['名字', '数值'], ['中文', '1']])
                self.assertEqual(read_csv_document(self.path)[1].encoding, 'utf-8')

    def test_empty_file_remains_empty(self):
        self.roundtrip(b'', [])
        self.roundtrip(codecs.BOM_UTF8, [], expected_output=b'')

    def test_writer_never_adds_bom_for_unicode_encoding_aliases(self):
        payload = '中文,value\r\n'.encode('utf-8')
        for encoding in ('utf-8', 'UTF_8_SIG', 'utf-16', 'utf-16-le', 'utf-16-be',
                         'utf-32', 'utf-32-le', 'utf-32-be'):
            with self.subTest(encoding=encoding):
                write_csv(self.path, [['中文', 'value']], encoding)
                self.assertEqual(self.path.read_bytes(), payload)

    def test_explicit_bom_metadata_does_not_write_a_header(self):
        write_csv(self.path, [['中文', 'value']],
                  csv_format=CsvFormat(encoding='utf-8-sig', bom=codecs.BOM_UTF8))
        self.assertEqual(self.path.read_bytes(), '中文,value\r\n'.encode('utf-8'))

    def test_enabled_bom_writes_exactly_one_matching_header(self):
        text = '中文,value\r\n'
        for encoding, payload_encoding, bom in (
            ('utf-8', 'utf-8', codecs.BOM_UTF8),
            ('UTF_8_SIG', 'utf-8', codecs.BOM_UTF8),
            ('utf-16', 'utf-16-le', codecs.BOM_UTF16_LE),
            ('utf-16-le', 'utf-16-le', codecs.BOM_UTF16_LE),
            ('utf-16-be', 'utf-16-be', codecs.BOM_UTF16_BE),
            ('utf-32', 'utf-32-le', codecs.BOM_UTF32_LE),
            ('utf-32-le', 'utf-32-le', codecs.BOM_UTF32_LE),
            ('utf-32-be', 'utf-32-be', codecs.BOM_UTF32_BE),
        ):
            with self.subTest(encoding=encoding):
                write_csv(self.path, [['中文', 'value']], encoding, write_bom=True)
                self.assertEqual(self.path.read_bytes(), bom + text.encode(payload_encoding))
                self.assertEqual(read_csv_document(self.path)[0], [['中文', 'value']])

    def test_bom_setting_leaves_gbk_and_gb18030_without_header(self):
        for encoding in ('gbk', 'gb18030'):
            with self.subTest(encoding=encoding):
                write_csv(self.path, [['中文', 'value']], encoding, write_bom=True)
                self.assertEqual(self.path.read_bytes(), '中文,value\r\n'.encode(encoding))

    def test_bom_toggle_preserves_data_and_format(self):
        payload = '名称;数值\n中文;1'.encode('utf-8')
        self.path.write_bytes(codecs.BOM_UTF8 + payload)
        for enabled in (False, True, False):
            rows, file_format = read_csv_document(self.path)
            write_csv(self.path, rows, csv_format=file_format, write_bom=enabled)
            self.assertEqual(self.path.read_bytes(), (codecs.BOM_UTF8 if enabled else b'') + payload)

    def test_bom_setting_derives_header_and_does_not_mutate_loaded_format(self):
        file_format = CsvFormat(encoding='utf-16-be', bom=codecs.BOM_UTF8)
        normalized = csv_format_for_save(file_format, write_bom=True)
        self.assertEqual(normalized.bom, codecs.BOM_UTF16_BE)
        self.assertEqual(file_format.bom, codecs.BOM_UTF8)
        self.assertEqual(csv_format_for_save(file_format).encoding, 'utf-8')
        write_csv(self.path, [['中文', 'value']], csv_format=file_format, write_bom=True)
        self.assertEqual(self.path.read_bytes(), codecs.BOM_UTF16_BE + '中文,value\r\n'.encode('utf-16-be'))

    def test_empty_file_respects_bom_switch(self):
        for encoding, bom in (('utf-8', codecs.BOM_UTF8),
                              ('utf-16-le', codecs.BOM_UTF16_LE),
                              ('utf-32-be', codecs.BOM_UTF32_BE)):
            with self.subTest(encoding=encoding):
                write_csv(self.path, [], encoding, write_bom=True)
                self.assertEqual(self.path.read_bytes(), bom)
                self.assertEqual(read_csv_document(self.path)[0], [])
                write_csv(self.path, [], encoding, write_bom=False)
                self.assertEqual(self.path.read_bytes(), b'')

    def test_complete_file_encoding_validation(self):
        raw = ('a' * 9000 + ',中文\n').encode('gbk')
        self.path.write_bytes(raw)
        rows, encoding = read_csv(self.path)
        self.assertEqual(encoding, 'gbk')
        self.assertEqual(detect_encoding(self.path), 'gbk')
        self.assertEqual(rows[0][1], '中文')

    def test_quoted_multiline_and_spaces_are_data(self):
        self.roundtrip(b'name,value\r\n"a\nb", spaces \r\n', [['name', 'value'], ['a\nb', ' spaces ']])

    def test_editing_plain_file_to_include_quotes_and_delimiters(self):
        self.path.write_bytes(b'name,value\r\na,b\r\n')
        rows, file_format = read_csv_document(self.path)
        rows[1][1] = 'Say "hello", then continue'
        write_csv(self.path, rows, csv_format=file_format)
        self.assertEqual(read_csv_document(self.path)[0], rows)

    def test_embedded_other_newline_is_quoted(self):
        for record_ending, embedded in (('\n', '\r'), ('\r', '\n')):
            with self.subTest(record_ending=repr(record_ending)):
                rows = [['a', f'b{embedded}c']]
                write_csv(self.path, rows, csv_format=CsvFormat(lineterminator=record_ending))
                self.assertEqual(read_csv_document(self.path)[0], rows)

    def test_new_blank_final_record_is_not_lost_without_terminal_newline(self):
        rows = [['value'], []]
        write_csv(self.path, rows, csv_format=CsvFormat(terminal_newline=False))
        self.assertEqual(read_csv_document(self.path)[0], rows)

    def test_long_cell_larger_than_csv_default_limit(self):
        self.path.write_text('value\n' + 'x' * 150000 + '\n', encoding='utf-8')
        self.assertEqual(len(read_csv_document(self.path)[0][1][0]), 150000)

    def test_malformed_quoted_file_is_rejected(self):
        self.path.write_bytes(b'a,b\n"unfinished,b\n')
        with self.assertRaises(csv.Error):
            read_csv_document(self.path)

    def test_encoding_failure_does_not_truncate_existing_file(self):
        self.path.write_bytes(b'original,data\r\n')
        with self.assertRaises(UnicodeEncodeError):
            write_csv(self.path, [['cannot encode', '🙂']], 'gbk')
        self.assertEqual(self.path.read_bytes(), b'original,data\r\n')
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_replace_failure_preserves_existing_and_cleans_temporary(self):
        self.path.write_bytes(b'original,data\r\n')
        with patch('csv_model.os.replace', side_effect=PermissionError('file locked')):
            with self.assertRaises(PermissionError):
                write_csv(self.path, [['new', 'data']])
        self.assertEqual(self.path.read_bytes(), b'original,data\r\n')
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
