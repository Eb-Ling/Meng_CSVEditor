import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QModelIndex, Qt
from PyQt5.QtTest import QSignalSpy
from PyQt5.QtWidgets import QApplication, QUndoStack

from csv_commands import (
    BatchEditCommand, CellEditCommand, ClearSelectionCommand,
    DeleteColsCommand, DeleteRowsCommand, InsertColsCommand, InsertRowsCommand, PasteCellsCommand,
)
from csv_model import CsvTableModel


class TableModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.model = CsvTableModel()
        self.stack = QUndoStack()

    def test_ragged_records_are_displayed_rectangular_but_exported_unchanged(self):
        rows = [['a', 'b', ''], ['c'], []]
        self.model.load_data(rows)
        self.assertEqual(self.model.columnCount(), 3)
        self.assertEqual(self.model.data(self.model.index(2, 2)), '')
        self.assertEqual(self.model.get_all_data(), rows)

    def test_edit_into_padding_and_undo_restores_original_field_count(self):
        self.model.load_data([['a'], ['b', 'c', 'd']])
        self.stack.push(CellEditCommand(self.model, 0, 2, '', 'new'))
        self.assertEqual(self.model.get_all_data()[0], ['a', '', 'new'])
        self.stack.undo()
        self.assertEqual(self.model.get_all_data()[0], ['a'])
        self.stack.redo()
        self.assertEqual(self.model.get_all_data()[0], ['a', '', 'new'])

    def test_deleting_all_rows_retains_width_and_undo_restores_all_cells(self):
        rows = [['a', 'b', 'c'], ['d']]
        self.model.load_data(rows)
        self.stack.push(DeleteRowsCommand(self.model, 0, 2, self.model.get_all_data()))
        self.assertEqual((self.model.rowCount(), self.model.columnCount()), (0, 3))
        self.stack.undo()
        self.assertEqual(self.model.get_all_data(), rows)

    def test_delete_column_undo_restores_each_row(self):
        rows = [['a', 'b', 'c'], ['d', 'e', 'f'], ['g']]
        self.model.load_data(rows)
        saved = [row[1:2] for row in self.model._data]
        self.stack.push(DeleteColsCommand(self.model, 1, 1, saved))
        self.assertEqual(self.model.get_all_data(), [['a', 'c'], ['d', 'f'], ['g']])
        self.stack.undo()
        self.assertEqual(self.model.get_all_data(), rows)
        self.stack.redo()
        self.stack.undo()
        self.assertEqual(self.model.get_all_data(), rows)

    def test_delete_multiple_and_final_columns_undo(self):
        for column, count in ((1, 2), (0, 3)):
            with self.subTest(column=column, count=count):
                rows = [['a', 'b', 'c'], ['d'], []]
                self.model.load_data(rows)
                saved = [row[column:column + count] for row in self.model._data]
                command = DeleteColsCommand(self.model, column, count, saved)
                command.redo()
                self.assertEqual(self.model.columnCount(), 3 - count)
                command.undo()
                self.assertEqual(self.model.get_all_data(), rows)

    def test_columns_exist_without_rows_and_rows_can_be_inserted(self):
        self.model.load_data([], column_count=2)
        self.assertTrue(self.model.insertColumns(1, 2))
        self.assertEqual(self.model.columnCount(), 4)
        self.assertTrue(self.model.insertRows(0, 1))
        self.assertEqual(self.model.get_all_data(), [['', '', '', '']])

    def test_invalid_structure_ranges_emit_no_change_signals(self):
        self.model.load_data([['a', 'b']])
        spies = [QSignalSpy(signal) for signal in (
            self.model.rowsInserted, self.model.rowsRemoved,
            self.model.columnsInserted, self.model.columnsRemoved)]
        for method, position, count in (
            (self.model.insertRows, -1, 1), (self.model.insertRows, 2, 1),
            (self.model.insertRows, 0, 0), (self.model.removeRows, 0, 2),
            (self.model.removeRows, 0, -1), (self.model.insertColumns, -1, 1),
            (self.model.insertColumns, 3, 1), (self.model.removeColumns, 2, 1),
            (self.model.removeColumns, 0, 0),
        ):
            self.assertFalse(method(position, count))
        self.assertTrue(all(len(spy) == 0 for spy in spies))
        self.assertEqual(self.model.get_all_data(), [['a', 'b']])
        self.assertEqual(self.model.rowCount(self.model.index(0, 0)), 0)
        self.assertEqual(self.model.columnCount(self.model.index(0, 0)), 0)
        self.assertEqual(self.model.flags(QModelIndex()), Qt.NoItemFlags)

    def test_paste_growth_and_mapped_rows_restore_dimensions_in_one_undo(self):
        rows = [['source0'], ['source1', 'second']]
        self.model.load_data(rows)
        cells = [(1, 0, 'source1', 'visible-first'), (0, 0, 'source0', 'visible-second'),
                 (2, 0, '', 'appended'), (2, 2, '', 'third-column')]
        self.stack.push(PasteCellsCommand(self.model, cells, 3, 3))
        self.assertEqual((self.model.rowCount(), self.model.columnCount()), (3, 3))
        self.assertEqual(self.model.data(self.model.index(1, 0)), 'visible-first')
        self.stack.undo()
        self.assertEqual((self.model.rowCount(), self.model.columnCount()), (2, 2))
        self.assertEqual(self.model.get_all_data(), rows)
        self.assertTrue(self.stack.isClean())
        self.stack.redo()
        self.assertEqual(self.model.data(self.model.index(2, 2)), 'third-column')

    def test_batch_edit_is_dirty_undoable_and_restores_ragged_fields(self):
        rows = [['a'], ['b', 'c']]
        self.model.load_data(rows)
        self.stack.push(BatchEditCommand(self.model, [(0, 0, 'a', 'x'), (0, 1, '', 'y')]))
        self.assertFalse(self.stack.isClean())
        self.assertEqual(self.model.get_all_data()[0], ['x', 'y'])
        self.stack.undo()
        self.assertTrue(self.stack.isClean())
        self.assertEqual(self.model.get_all_data(), rows)

    def test_clear_selection_undo_preserves_explicit_empty_trailing_fields(self):
        rows = [['a', 'b', ''], ['c']]
        self.model.load_data(rows)
        self.stack.push(ClearSelectionCommand(self.model, [(0, 1, 'b'), (1, 0, 'c')]))
        self.stack.undo()
        self.assertEqual(self.model.get_all_data(), rows)

    def test_insert_commands_restore_original_dimensions(self):
        rows = [['a'], []]
        self.model.load_data(rows)
        self.stack.push(InsertRowsCommand(self.model, 1))
        self.stack.push(InsertColsCommand(self.model, 0))
        self.stack.undo()
        self.stack.undo()
        self.assertEqual(self.model.get_all_data(), rows)
        self.assertEqual((self.model.rowCount(), self.model.columnCount()), (2, 1))


if __name__ == '__main__':
    unittest.main()
