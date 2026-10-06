import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QModelIndex, Qt
from PyQt5.QtTest import QSignalSpy
from PyQt5.QtWidgets import QApplication

from csv_model import CsvTableModel


class BadText:
    def __str__(self):
        raise ValueError('invalid text conversion')


class ModelBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.model = CsvTableModel()
        self.model.load_data([['a'], [], ['b', 'c']])

    def snapshot(self):
        return (self.model.get_all_data(), self.model.columnCount(),
                self.model.get_row_lengths(), self.model._document_generation)

    def test_foreign_index_cannot_read_or_modify_model(self):
        other = CsvTableModel()
        other.load_data([['foreign']])
        index = other.index(0, 0)
        self.assertFalse(self.model.setData(index, 'changed'))
        self.assertEqual(self.model.flags(index), Qt.NoItemFlags)
        self.assertFalse(self.model.data(index).isValid())
        self.assertEqual(self.model.get_all_data()[0], ['a'])

    def test_stale_out_of_bounds_index_is_harmless(self):
        stale = self.model.index(2, 1)
        self.model.load_data([['new']])
        self.assertFalse(self.model.setData(stale, 'changed'))
        self.assertFalse(self.model.data(stale).isValid())
        self.assertEqual(self.model.flags(stale), Qt.NoItemFlags)

    def test_non_index_objects_and_invalid_roles_are_harmless(self):
        for index in (None, 0, 'index', QModelIndex()):
            self.assertFalse(self.model.setData(index, 'changed'))
            self.assertEqual(self.model.flags(index), Qt.NoItemFlags)
            self.assertFalse(self.model.data(index).isValid())
        self.assertFalse(self.model.setData(self.model.index(0, 0), 'changed', Qt.DisplayRole))
        self.assertFalse(self.model.data(self.model.index(0, 0), Qt.DecorationRole).isValid())

    def test_invalid_load_width_never_starts_reset_or_changes_document(self):
        original = self.snapshot()
        reset = QSignalSpy(self.model.modelAboutToBeReset)
        for width in (-1, True, 1.5, '2', 2 ** 31):
            with self.subTest(width=width), self.assertRaises(ValueError):
                self.model.load_data([['new']], column_count=width)
            self.assertEqual(self.snapshot(), original)
        self.assertEqual(len(reset), 0)

    def test_failed_load_conversion_or_iteration_is_atomic(self):
        original = self.snapshot()
        reset = QSignalSpy(self.model.modelAboutToBeReset)

        def broken_rows():
            yield ['first']
            raise RuntimeError('read failed')

        for rows in ([[BadText()]], broken_rows(), 'abc', ['abc'], None):
            with self.subTest(rows=type(rows).__name__), self.assertRaises((ValueError, TypeError, RuntimeError)):
                self.model.load_data(rows)
            self.assertEqual(self.snapshot(), original)
        self.assertEqual(len(reset), 0)

    def test_failed_header_allocation_never_starts_reset(self):
        original = self.snapshot()
        reset = QSignalSpy(self.model.modelAboutToBeReset)
        with patch.object(self.model, '_col_letter', side_effect=MemoryError('allocation')):
            with self.assertRaises(MemoryError):
                self.model.load_data([['new']])
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(len(reset), 0)

    def test_invalid_structure_types_and_int_overflow_emit_no_notifications(self):
        original = self.snapshot()
        notifications = [QSignalSpy(signal) for signal in (
            self.model.rowsAboutToBeInserted, self.model.rowsAboutToBeRemoved,
            self.model.columnsAboutToBeInserted, self.model.columnsAboutToBeRemoved)]
        for operation in (self.model.insertRows, self.model.removeRows,
                          self.model.insertColumns, self.model.removeColumns):
            for position, count in ((0.5, 1), (0, 1.5), (True, 1), (0, False),
                                    ('0', 1), (0, '1'), (None, 1), (0, None),
                                    (2 ** 31, 1), (0, 2 ** 31), (0, 2 ** 31 - 1)):
                with self.subTest(operation=operation.__name__, position=position, count=count):
                    self.assertFalse(operation(position, count))
                    self.assertEqual(self.snapshot(), original)
            self.assertFalse(operation(0, 1, None))
            self.assertFalse(operation(0, 1, self.model.index(0, 0)))
        self.assertTrue(all(len(spy) == 0 for spy in notifications))

    def test_failed_structural_allocation_keeps_data_and_signals_unchanged(self):
        original = self.snapshot()
        inserted = QSignalSpy(self.model.columnsAboutToBeInserted)
        removed = QSignalSpy(self.model.columnsAboutToBeRemoved)
        with patch.object(self.model, '_col_letter', side_effect=MemoryError('allocation')):
            self.assertFalse(self.model.insertColumns(1, 1))
            self.assertFalse(self.model.removeColumns(0, 1))
        self.assertEqual(self.snapshot(), original)
        self.assertEqual((len(inserted), len(removed)), (0, 0))

    def test_bad_cell_coordinates_or_values_do_not_change_data(self):
        original = self.snapshot()
        changed = QSignalSpy(self.model.dataChanged)
        for row, col in ((-1, 0), (0, -1), (0.5, 0), (0, True), ('0', 0),
                         (None, 0), (0, 2 ** 31), (3, 0), (0, 2)):
            self.assertFalse(self.model._set_data(row, col, 'new'))
        self.assertFalse(self.model._set_data(0, 0, BadText()))
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(len(changed), 0)

    def test_batch_conversion_failure_does_not_partially_change_cells(self):
        original = self.snapshot()
        changed = QSignalSpy(self.model.dataChanged)
        self.assertFalse(self.model._apply_cells([(0, 0, 'new'), (2, 1, BadText())]))
        self.assertFalse(self.model._apply_cells([(0, 0, 'new'), (20, 1, 'bad')]))
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(len(changed), 0)

    def test_failed_paste_preparation_does_not_grow_grid(self):
        original = self.snapshot()
        inserted = [QSignalSpy(signal) for signal in
                    (self.model.rowsAboutToBeInserted, self.model.columnsAboutToBeInserted)]
        self.assertFalse(self.model._resize_and_apply(4, 4, [(0, 0, 'new'), (3, 3, BadText())]))
        self.assertFalse(self.model._resize_and_apply(4, 4, [(4, 0, 'out of range')]))
        with patch.object(self.model, '_col_letter', side_effect=MemoryError('allocation')):
            self.assertFalse(self.model._resize_and_apply(4, 4, [(3, 3, 'new')]))
        self.assertEqual(self.snapshot(), original)
        self.assertTrue(all(len(spy) == 0 for spy in inserted))

    def test_invalid_headers_and_blocks_return_empty_values(self):
        for section in (-1, 1.5, None, True, '0', 2 ** 31):
            self.assertFalse(self.model.headerData(section, Qt.Horizontal).isValid())
        self.assertFalse(self.model.headerData(2, Qt.Horizontal).isValid())
        self.assertFalse(self.model.headerData(3, Qt.Vertical).isValid())
        self.assertFalse(self.model.headerData(0, -1).isValid())
        for args in ((-1, 0, 1, 1), (0, 0, 1.5, 1), (0, 0, 1, None)):
            self.assertEqual(self.model.get_block(*args), [])
        self.assertEqual(self.model.get_block(2, 1, 2, 2), [['c', ''], ['', '']])

    def test_empty_dimensions_and_ragged_resize_keep_record_lengths(self):
        self.model.load_data([[], []], column_count=0)
        self.assertTrue(self.model._resize_and_apply(3, 2, [(2, 1, 'last')]))
        self.assertEqual(self.model.get_all_data(), [['', ''], ['', ''], ['', 'last']])
        self.assertTrue(self.model._resize_and_apply(0, 0, []))
        self.assertEqual((self.model.rowCount(), self.model.columnCount()), (0, 0))


if __name__ == '__main__':
    unittest.main()
