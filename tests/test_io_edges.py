"""CSV boundary regressions, failure injection, and deterministic round trips."""
import codecs
import csv
import io
import os
import random
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from csv_model import CsvFormat, read_csv_document, write_csv


class CsvIoBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'boundary.csv'
        self.original = b'original,data\r\n'

    def assert_original_and_no_temporary_files(self):
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_delimiter_after_long_first_field_is_detected(self):
        for delimiter in (';', '|', '\t'):
            with self.subTest(delimiter=repr(delimiter)):
                rows = [['x' * 160000, 'b'], ['q', 'r']]
                write_csv(self.path, rows, csv_format=CsvFormat(delimiter=delimiter))
                actual, file_format = read_csv_document(self.path)
                self.assertEqual(file_format.delimiter, delimiter)
                self.assertEqual(actual, rows)

    def test_ragged_quoted_csv_rejects_incorrect_sniffer_candidate(self):
        rows = [['h1', 'h2', 'h3', 'h4', 'h5', 'h6'], [],
                [',"', 'a",", |a\r\r||']]
        write_csv(self.path, rows, csv_format=CsvFormat(
            delimiter=';', lineterminator='\r', terminal_newline=False))
        actual, file_format = read_csv_document(self.path)
        self.assertEqual(file_format.delimiter, ';')
        self.assertEqual(actual, rows)

    def test_fixed_seed_random_standard_csv_round_trips(self):
        randomizer = random.Random(924)
        alphabet = 'a中,;|\t"\r\n '
        for case_number in range(800):
            file_format = CsvFormat(
                delimiter=randomizer.choice((',', ';', '|', '\t')),
                lineterminator=randomizer.choice(('\n', '\r', '\r\n')),
                terminal_newline=randomizer.choice((False, True)),
            )
            rows = [['h1', 'h2', 'h3', 'h4', 'h5', 'h6']]
            for _ in range(randomizer.randrange(1, 12)):
                rows.append([''.join(randomizer.choice(alphabet)
                                     for _ in range(randomizer.randrange(0, 24)))
                             for _ in range(randomizer.randrange(0, 7))])
            with self.subTest(case=case_number, delimiter=repr(file_format.delimiter),
                              ending=repr(file_format.lineterminator)):
                write_csv(self.path, rows, csv_format=file_format)
                self.assertEqual(read_csv_document(self.path)[0], rows)

    def test_single_empty_field_and_empty_records_stay_distinct(self):
        rows = [[], [''], [], ['', ''], ['value'], [], []]
        for ending in ('\r', '\n', '\r\n'):
            for final_newline in (False, True):
                with self.subTest(ending=repr(ending), final_newline=final_newline):
                    write_csv(self.path, rows, csv_format=CsvFormat(
                        lineterminator=ending, terminal_newline=final_newline))
                    self.assertEqual(read_csv_document(self.path)[0], rows)

    def test_each_embedded_record_ending_is_cell_data(self):
        rows = [['id', 'content'], ['1', '\r\n\n\r\r\n'],
                ['2', '"quoted",\t;|'], ['3', '  preserved  ']]
        for delimiter in (',', ';', '|', '\t'):
            for ending in ('\r', '\n', '\r\n'):
                with self.subTest(delimiter=repr(delimiter), ending=repr(ending)):
                    write_csv(self.path, rows, csv_format=CsvFormat(
                        delimiter=delimiter, lineterminator=ending))
                    self.assertEqual(read_csv_document(self.path)[0], rows)

    def test_explicit_escape_dialect_writer_remains_valid(self):
        # Escape-based dialects are explicitly verified without assuming that
        # an automatic detector can infer every nonstandard CSV convention.
        rows = [['a"b', 'x,y', '\\', 'a\r\nb'], ['', '中文']]
        file_format = CsvFormat(doublequote=False, escapechar='\\')
        write_csv(self.path, rows, csv_format=file_format)
        parsed = list(csv.reader(io.StringIO(self.path.read_bytes().decode('utf-8'), newline=''),
                                 delimiter=',', quotechar='"', doublequote=False,
                                 escapechar='\\', strict=True))
        self.assertEqual(parsed, rows)

    def test_literal_apostrophe_fields_do_not_become_single_quote_dialect(self):
        examples = (
            [['name', 'value'], ['a', "'literal'"], ['b', 'plain']],
            [['h1', 'h2', 'h3', 'h4'],
             ["'one'", "'two'", "O'Reilly", "''"],
             ["'three'", "'a,b'", "'", ''],
             ['plain', "'one;two'", "'pipe|value'", "'tab\tvalue'"]],
        )
        for rows in examples:
            for delimiter in (',', ';', '|', '\t'):
                for ending in ('\r', '\n', '\r\n'):
                    with self.subTest(rows=len(rows), delimiter=repr(delimiter),
                                      ending=repr(ending)):
                        write_csv(self.path, rows, csv_format=CsvFormat(
                            delimiter=delimiter, lineterminator=ending))
                        actual, file_format = read_csv_document(self.path)
                        self.assertEqual(actual, rows)
                        self.assertEqual(file_format.quotechar, '"')

    def test_non_ascii_path_and_pathlike_input(self):
        self.path = self.path.with_name('边界 数据.csv')
        rows = [['名称', 'value'], ['🙂', 'line\ncontent']]
        write_csv(self.path, rows)
        self.assertEqual(read_csv_document(self.path)[0], rows)

    def test_empty_bom_only_unicode_files(self):
        for encoding, bom in (
            ('utf-8', codecs.BOM_UTF8), ('utf-16-le', codecs.BOM_UTF16_LE),
            ('utf-16-be', codecs.BOM_UTF16_BE), ('utf-32-le', codecs.BOM_UTF32_LE),
            ('utf-32-be', codecs.BOM_UTF32_BE),
        ):
            with self.subTest(encoding=encoding):
                self.path.write_bytes(bom)
                rows, file_format = read_csv_document(self.path)
                self.assertEqual(rows, [])
                write_csv(self.path, rows, csv_format=file_format, write_bom=True)
                self.assertEqual(self.path.read_bytes(), bom)
                write_csv(self.path, rows, csv_format=file_format, write_bom=False)
                self.assertEqual(self.path.read_bytes(), b'')

    def test_literal_leading_feff_is_cell_data_under_either_bom_setting(self):
        rows = [['\ufeffliteral', 'data'], ['second', '\ufeffinside']]
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                write_csv(self.path, rows, write_bom=enabled)
                if not enabled:
                    self.assertFalse(self.path.read_bytes().startswith(codecs.BOM_UTF8))
                self.assertEqual(read_csv_document(self.path)[0], rows)

    def test_leading_feff_first_record_generator_is_consumed_once(self):
        expected = [['\ufeffliteral', 'data'], ['second', 'value']]
        for enabled in (False, True):
            calls = []

            def first_record():
                calls.append('first')
                yield '\ufeffliteral'
                yield 'data'

            def records():
                calls.append('outer')
                yield first_record()
                yield iter(['second', 'value'])

            with self.subTest(enabled=enabled):
                write_csv(self.path, records(), write_bom=enabled)
                self.assertEqual(calls, ['outer', 'first'])
                self.assertEqual(read_csv_document(self.path)[0], expected)

    def test_incomplete_bom_encoded_code_units_fail_cleanly(self):
        for raw in (codecs.BOM_UTF16_LE + b'x', codecs.BOM_UTF16_BE + b'x',
                    codecs.BOM_UTF32_LE + b'abc', codecs.BOM_UTF32_BE + b'abc',
                    codecs.BOM_UTF8 + b'\xff'):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                with self.assertRaises(UnicodeDecodeError):
                    read_csv_document(self.path)

    def test_csv_global_field_limit_is_restored_on_success_and_error(self):
        original_limit = csv.field_size_limit()
        self.addCleanup(csv.field_size_limit, original_limit)
        csv.field_size_limit(32)
        self.path.write_bytes(b'a,b\n' + b'x' * 150000 + b',c\n')
        self.assertEqual(len(read_csv_document(self.path)[0][1][0]), 150000)
        self.assertEqual(csv.field_size_limit(), 32)
        self.path.write_bytes(b'a,b\n"' + b'x' * 150000)
        with self.assertRaises(csv.Error):
            read_csv_document(self.path)
        self.assertEqual(csv.field_size_limit(), 32)

    def test_concurrent_large_csv_reads_restore_process_field_limit(self):
        original_limit = csv.field_size_limit()
        self.addCleanup(csv.field_size_limit, original_limit)
        csv.field_size_limit(16)
        inputs = []
        for number, delimiter in enumerate((',', ';', '|', '\t')):
            path = self.path.with_name(f'parallel-{number}.csv')
            rows = [['name', 'data'], ['x' * 180000, 'value\ncontent']]
            write_csv(path, rows, csv_format=CsvFormat(delimiter=delimiter))
            inputs.append((path, rows))

        def read_and_compare(item):
            path, rows = item
            return read_csv_document(path)[0] == rows

        with ThreadPoolExecutor(max_workers=4) as executor:
            self.assertTrue(all(executor.map(read_and_compare, inputs * 2)))
        self.assertEqual(csv.field_size_limit(), 16)

    def test_invalid_format_objects_are_not_silently_replaced_by_defaults(self):
        self.path.write_bytes(self.original)
        for invalid in (False, 0, '', [], {}, object()):
            with self.subTest(invalid=repr(invalid)):
                with self.assertRaises(TypeError):
                    write_csv(self.path, [['new', 'data']], csv_format=invalid)
                self.assert_original_and_no_temporary_files()

    def test_invalid_record_terminators_are_rejected_without_data_loss(self):
        self.path.write_bytes(self.original)
        for ending in ('', 'END', '\n\n', '\x00', None):
            with self.subTest(ending=repr(ending)):
                with self.assertRaises((TypeError, ValueError)):
                    write_csv(self.path, [['new', 'data'], ['next', 'record']],
                              csv_format=CsvFormat(lineterminator=ending))
                self.assert_original_and_no_temporary_files()

    def test_invalid_delimiter_and_quote_parameters_keep_original(self):
        self.path.write_bytes(self.original)
        for file_format in (CsvFormat(delimiter=''), CsvFormat(delimiter=';;'),
                            CsvFormat(delimiter='\r'), CsvFormat(delimiter='\n'),
                            CsvFormat(delimiter=None), CsvFormat(quotechar=''),
                            CsvFormat(quotechar='xx'), CsvFormat(escapechar='xx')):
            with self.subTest(file_format=file_format):
                with self.assertRaises((TypeError, ValueError, csv.Error)):
                    write_csv(self.path, [['new', 'data']], csv_format=file_format)
                self.assert_original_and_no_temporary_files()

    def test_unknown_codec_never_touches_existing_file(self):
        self.path.write_bytes(self.original)
        with self.assertRaises(LookupError):
            write_csv(self.path, [['new', 'data']], encoding='not-a-real-codec')
        self.assert_original_and_no_temporary_files()

    def test_iterator_failure_mid_save_preserves_original(self):
        self.path.write_bytes(self.original)

        def rows():
            yield ['first', 'record']
            raise RuntimeError('row generator failed')

        with self.assertRaisesRegex(RuntimeError, 'row generator failed'):
            write_csv(self.path, rows())
        self.assert_original_and_no_temporary_files()

    def test_non_iterable_record_preserves_original(self):
        self.path.write_bytes(self.original)
        with self.assertRaises((TypeError, csv.Error)):
            write_csv(self.path, [['valid'], 7])
        self.assert_original_and_no_temporary_files()

    def test_fsync_failure_preserves_original_and_cleans_temporary(self):
        self.path.write_bytes(self.original)
        with patch('csv_model.os.fsync', side_effect=OSError('sync failed')):
            with self.assertRaisesRegex(OSError, 'sync failed'):
                write_csv(self.path, [['new', 'data']])
        self.assert_original_and_no_temporary_files()

    def test_chmod_failure_preserves_original_and_cleans_temporary(self):
        self.path.write_bytes(self.original)
        with patch('csv_model.os.chmod', side_effect=OSError('chmod failed')):
            with self.assertRaisesRegex(OSError, 'chmod failed'):
                write_csv(self.path, [['new', 'data']])
        self.assert_original_and_no_temporary_files()

    def test_text_wrapper_construction_failure_closes_descriptor(self):
        self.path.write_bytes(self.original)
        with patch('csv_model.io.TextIOWrapper', side_effect=LookupError('wrapper failed')):
            with self.assertRaisesRegex(LookupError, 'wrapper failed'):
                write_csv(self.path, [['new', 'data']])
        self.assert_original_and_no_temporary_files()

    def test_fdopen_failure_closes_raw_descriptor(self):
        self.path.write_bytes(self.original)
        real_mkstemp = tempfile.mkstemp
        descriptors = []

        def record_descriptor(*args, **kwargs):
            descriptor, path = real_mkstemp(*args, **kwargs)
            descriptors.append(descriptor)
            return descriptor, path

        try:
            with patch('csv_model.tempfile.mkstemp', side_effect=record_descriptor), \
                    patch('csv_model.os.fdopen', side_effect=OSError('fdopen failed')):
                with self.assertRaisesRegex(OSError, 'fdopen failed'):
                    write_csv(self.path, [['new', 'data']])
            self.assertEqual(len(descriptors), 1)
            with self.assertRaises(OSError):
                os.fstat(descriptors[0])
            self.assert_original_and_no_temporary_files()
        finally:
            # Release a descriptor even when running against the broken build.
            for descriptor in descriptors:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            for temporary_path in self.path.parent.glob('*.tmp'):
                temporary_path.unlink()

    def test_cleanup_failure_does_not_hide_primary_save_error(self):
        self.path.write_bytes(self.original)
        try:
            with patch('csv_model.os.replace', side_effect=OSError('replace failed')), \
                    patch('csv_model.os.unlink', side_effect=PermissionError('cleanup failed')):
                with self.assertRaisesRegex(OSError, 'replace failed'):
                    write_csv(self.path, [['new', 'data']])
            self.assertEqual(self.path.read_bytes(), self.original)
        finally:
            for temporary_path in self.path.parent.glob('*.tmp'):
                temporary_path.unlink()

    def test_missing_parent_and_directory_destinations_fail_without_artifacts(self):
        with self.assertRaises(OSError):
            write_csv(self.path.parent / 'absent' / 'input.csv', [['value']])
        with self.assertRaises(OSError):
            write_csv(self.path.parent, [['value']])
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_missing_file_directory_and_nul_read_paths_fail_cleanly(self):
        for path in (self.path, self.path.parent, str(self.path) + '\x00'):
            with self.subTest(path=repr(path)):
                with self.assertRaises((OSError, ValueError)):
                    read_csv_document(path)

    @unittest.skipUnless(os.name == 'nt', 'Windows readonly-file cleanup regression')
    def test_readonly_destination_save_failure_leaves_no_readonly_temporary(self):
        self.path.write_bytes(self.original)
        os.chmod(self.path, stat.S_IREAD)
        try:
            with self.assertRaises(PermissionError):
                write_csv(self.path, [['new', 'data']])
            self.assert_original_and_no_temporary_files()
        finally:
            os.chmod(self.path, stat.S_IWRITE)
            for temporary_path in self.path.parent.glob('*.tmp'):
                os.chmod(temporary_path, stat.S_IWRITE)
                temporary_path.unlink()


if __name__ == '__main__':
    unittest.main()
