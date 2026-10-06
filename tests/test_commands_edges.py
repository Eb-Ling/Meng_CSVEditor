import copy
import os
import random
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5 import sip
from PyQt5.QtCore import qInstallMessageHandler
from PyQt5.QtTest import QAbstractItemModelTester, QSignalSpy
from PyQt5.QtWidgets import QApplication, QUndoStack

from csv_commands import (BatchEditCommand, CellEditCommand, ClearSelectionCommand,
                          DeleteColsCommand, DeleteRowsCommand, InsertColsCommand,
                          InsertRowsCommand, PasteCellsCommand, PasteCommand, SafeUndoStack)
from csv_model import CsvTableModel


class BadText:
    def __str__(self):
        raise ValueError('invalid field')


class CommandBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.model = CsvTableModel()
        self.model.load_data([['a'], [], ['b', 'c']])
        self.stack = SafeUndoStack()

    def snapshot(self):
        return self.model.get_all_data(), self.model.columnCount(), self.model.get_row_lengths()

    def test_duplicate_edits_keep_actual_initial_value_and_last_new_value(self):
        before = self.snapshot()
        self.stack.push(BatchEditCommand(self.model, [(0, 0, 'wrong', 'first'),
                                                     (0, 0, 'wrong again', 'last')]))
        self.assertEqual(self.model.get_all_data()[0], ['last'])
        self.stack.undo()
        self.assertEqual(self.snapshot(), before)
        self.stack.redo()
        self.assertEqual(self.model.get_all_data()[0], ['last'])

    def test_invalid_batches_never_partly_edit_or_dirty_stack(self):
        before = self.snapshot()
        for cells in ([(0, 0, 'a', 'new'), (100, 0, '', 'bad')],
                      [(0, 0, 'a', 'new'), (2, 1, 'c', BadText())],
                      [(0.5, 0, '', 'bad')], [(True, 0, '', 'bad')],
                      [(0, 0, 'not enough')], None):
            with self.subTest(cells=type(cells).__name__):
                self.stack.push(BatchEditCommand(self.model, cells))
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.stack.count(), 0)
                self.assertTrue(self.stack.isClean())

    def test_empty_and_unchanged_edits_do_not_add_undo_steps(self):
        for command in (BatchEditCommand(self.model, []),
                        CellEditCommand(self.model, 0, 0, 'incorrect old', 'a'),
                        ClearSelectionCommand(self.model, [(1, 0, '')])):
            self.stack.push(command)
        self.assertEqual(self.stack.count(), 0)
        self.assertTrue(self.stack.isClean())

    def test_invalid_structure_commands_leave_no_history(self):
        before = self.snapshot()
        for position, count in ((-1, 1), (0.5, 1), (0, None), (0, 2 ** 31),
                                (0, 2 ** 31 - 1), (100, 1), (0, 0), (0, -1)):
            for command_type in (InsertRowsCommand, InsertColsCommand, DeleteRowsCommand, DeleteColsCommand):
                args = (self.model, position, count)
                if command_type in (DeleteRowsCommand, DeleteColsCommand):
                    args += (None,)
                self.stack.push(command_type(*args))
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.stack.count(), 0)

    def test_delete_undo_uses_model_snapshot_even_when_caller_snapshot_is_wrong(self):
        before = self.snapshot()
        for command in (DeleteRowsCommand(self.model, 0, 2, [['wrong']]),
                        DeleteColsCommand(self.model, 0, 1, None)):
            command.redo()
            command.undo()
            self.assertEqual(self.snapshot(), before)

    def test_commands_are_idempotent_when_called_repeatedly(self):
        before = self.snapshot()
        for command in (InsertRowsCommand(self.model, 1, 2), InsertColsCommand(self.model, 1, 2),
                        DeleteRowsCommand(self.model, 0, 1, []), DeleteColsCommand(self.model, 0, 1, []),
                        CellEditCommand(self.model, 0, 0, 'a', 'new'),
                        PasteCellsCommand(self.model, [(3, 3, '', 'new')], 4, 4)):
            command.redo()
            after = self.snapshot()
            command.redo()
            self.assertEqual(self.snapshot(), after)
            command.undo()
            self.assertEqual(self.snapshot(), before)
            command.undo()
            self.assertEqual(self.snapshot(), before)

    def test_document_reset_invalidates_old_edit_structure_and_paste(self):
        constructors = (lambda: CellEditCommand(self.model, 0, 0, 'a', 'new'),
                        lambda: InsertRowsCommand(self.model, 0),
                        lambda: DeleteColsCommand(self.model, 0, 1, []),
                        lambda: PasteCellsCommand(self.model, [(3, 3, '', 'new')], 4, 4))
        for construct in constructors:
            self.model.load_data([['a'], [], ['b', 'c']])
            command = construct()
            command.redo()
            self.model.load_data([['different', 'document']])
            before = self.snapshot()
            command.undo()
            command.redo()
            self.assertEqual(self.snapshot(), before)
            self.assertTrue(command.isObsolete())

    def test_stale_edit_does_not_overwrite_changed_cell(self):
        command = CellEditCommand(self.model, 0, 0, 'a', 'new')
        self.model.setData(self.model.index(0, 0), 'outside edit')
        command.redo()
        self.assertEqual(self.model.get_all_data()[0], ['outside edit'])
        self.assertTrue(command.isObsolete())

    def test_stale_structure_shape_and_deleted_model_are_safe(self):
        command = InsertRowsCommand(self.model, 0)
        self.model.insertColumns(0, 1)
        command.redo()
        self.assertEqual(self.model.rowCount(), 3)
        self.assertTrue(command.isObsolete())
        for command_type in (lambda model: CellEditCommand(model, 0, 0, '', 'new'),
                             lambda model: InsertRowsCommand(model, 0),
                             lambda model: PasteCellsCommand(model, [], 4, 4)):
            model = CsvTableModel()
            model.load_data([['']])
            command = command_type(model)
            sip.delete(model)
            command.redo()
            command.undo()
            self.assertTrue(command.isObsolete())

    def test_failed_paste_and_delete_undo_preparation_are_atomic(self):
        before = self.snapshot()
        self.stack.push(PasteCellsCommand(self.model, [(3, 3, '', 'new')], 4, 4))
        self.stack.undo()
        with patch.object(self.model, '_col_letter', side_effect=MemoryError('allocation')):
            self.stack.redo()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.stack.count(), self.stack.index()), (1, 0))
        self.assertTrue(self.stack.canRedo())
        deletion = DeleteColsCommand(self.model, 0, 1, None)
        deletion.redo()
        after_delete = self.snapshot()
        with patch.object(self.model, '_col_letter', side_effect=MemoryError('allocation')):
            deletion.undo()
        self.assertEqual(self.snapshot(), after_delete)
        self.assertFalse(deletion.isObsolete())
        deletion.undo()
        self.assertEqual(self.snapshot(), before)

    def test_operation_failures_report_once_but_invalid_commands_are_silent(self):
        failed = QSignalSpy(self.model.operationFailed)
        self.stack.push(InsertRowsCommand(self.model, -1, 1))
        self.assertEqual(len(failed), 0)
        with patch.object(self.model, 'insertRows', side_effect=MemoryError('allocation')):
            self.stack.push(InsertRowsCommand(self.model, 1, 1))
        self.assertEqual(len(failed), 1)
        self.assertIn('内存不足', failed[0][0])
        with patch.object(self.model, '_resize_and_apply', return_value=False):
            self.stack.push(PasteCellsCommand(self.model, [(3, 3, '', 'new')], 4, 4))
        self.assertEqual(len(failed), 2)
        self.assertIn('保持不变', failed[1][0])

    def test_invalid_paste_targets_and_compatibility_inputs_are_safe(self):
        before = self.snapshot()
        commands = [PasteCellsCommand(self.model, [], rows, cols) for rows, cols in
                    ((-1, 3), (3, None), (3.5, 3), (3, 2 ** 31), (True, 2))]
        commands += [PasteCellsCommand(self.model, [(4, 0, '', 'bad')], 4, 4),
                     PasteCommand(self.model, -1, 0, [], [['bad']]),
                     PasteCommand(self.model, 0, 0, [], None)]
        for command in commands:
            self.stack.push(command)
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(self.stack.count(), 0)

    def test_qt_virtual_method_failures_do_not_abort_process(self):
        code = textwrap.dedent('''
            from PyQt5 import sip
            from PyQt5.QtWidgets import QApplication, QUndoStack
            from csv_model import CsvTableModel
            from csv_commands import BatchEditCommand, InsertRowsCommand, PasteCellsCommand
            app = QApplication([])
            model = CsvTableModel()
            model.load_data([['a']])
            stack = QUndoStack()
            stack.push(InsertRowsCommand(model, 0, 'bad'))
            stack.push(BatchEditCommand(model, [(0, 0, 'a', 'new'), (2, 0, '', 'bad')]))
            command = PasteCellsCommand(model, [(1, 1, '', 'new')], 2, 2)
            sip.delete(model)
            stack.push(command)
            stack.undo()
            stack.redo()
            assert stack.count() == 0
            print('survived')
        ''')
        result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('survived', result.stdout)

    def test_zero_dimension_changes_with_real_sorted_view_do_not_abort_process(self):
        code = textwrap.dedent('''
            from PyQt5.QtCore import QSortFilterProxyModel, Qt
            from PyQt5.QtWidgets import QApplication, QTableView, QUndoStack
            from csv_model import CsvTableModel
            from csv_commands import DeleteColsCommand, DeleteRowsCommand, PasteCellsCommand
            app = QApplication([])
            model = CsvTableModel()
            model.load_data([[str(row), 'field'] for row in range(12)])
            proxy = QSortFilterProxyModel()
            proxy.setSourceModel(model)
            proxy.setDynamicSortFilter(True)
            proxy.sort(0, Qt.AscendingOrder)
            view = QTableView()
            view.setModel(proxy)
            view.show()
            stack = QUndoStack()
            for iteration in range(20):
                stack.push(DeleteColsCommand(model, 0, 2, None))
                stack.push(DeleteRowsCommand(model, 0, 12, None))
                app.processEvents()
                stack.push(PasteCellsCommand(model, [(0, 0, '', 'new')], 1, 1))
                app.processEvents()
                stack.undo()
                stack.undo()
                stack.undo()
                app.processEvents()
                assert model.get_all_data() == [[str(row), 'field'] for row in range(12)]
            print('zero-dimension survived')
        ''')
        result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('zero-dimension survived', result.stdout)

    def test_seeded_random_commands_undo_redo_match_independent_records(self):
        warnings = []
        previous_handler = qInstallMessageHandler(lambda kind, context, message: warnings.append(message))
        try:
            for seed in (0, 17, 736, 20261005, 314159):
                with self.subTest(seed=seed):
                    self._random_history(seed)
        finally:
            qInstallMessageHandler(previous_handler)
        self.assertEqual(warnings, [])

    def _random_history(self, seed):
        rng = random.Random(seed)
        self.stack.clear()
        initial = [['a', ''], [], ['b']]
        self.model.load_data(initial, column_count=3)
        tester = QAbstractItemModelTester(self.model, QAbstractItemModelTester.FailureReportingMode.Warning)
        state = {'data': [row + [''] * (3 - len(row)) for row in initial],
                 'lengths': [len(row) for row in initial], 'width': 3}
        history, cursor = [copy.deepcopy(state)], 0

        def check():
            nonlocal tester
            self.assertEqual(self.model._data, state['data'], (seed, cursor))
            self.assertEqual(self.model.get_row_lengths(), state['lengths'], (seed, cursor))
            self.assertEqual(self.model.columnCount(), state['width'], (seed, cursor))
            self.assertEqual(self.model.get_all_data(), [row[:length] for row, length in
                                                       zip(state['data'], state['lengths'])], (seed, cursor))
            self.assertEqual(self.stack.index(), cursor)
            # Qt 5.15's diagnostic tester itself crashes when removing rows
            # from a zero-column table, including QStandardItemModel. Test
            # that valid state with the real view/proxy subprocess below.
            if self.model.columnCount() == 0 and tester is not None:
                sip.delete(tester)
                tester = None
            elif self.model.columnCount() and tester is None:
                tester = QAbstractItemModelTester(self.model, QAbstractItemModelTester.FailureReportingMode.Warning)

        for step in range(200):
            action = rng.choice(('undo', 'redo', 'insert_row', 'delete_row', 'insert_col',
                                 'delete_col', 'edit', 'paste'))
            rows, cols = len(state['data']), state['width']
            if os.environ.get('CSV_TEST_TRACE'):
                print(seed, step, action, rows, cols, flush=True)
            if action == 'undo':
                if cursor:
                    self.stack.undo()
                    cursor -= 1
                    state = copy.deepcopy(history[cursor])
                check()
                continue
            if action == 'redo':
                if cursor + 1 < len(history):
                    self.stack.redo()
                    cursor += 1
                    state = copy.deepcopy(history[cursor])
                check()
                continue
            next_state = copy.deepcopy(state)
            data, lengths = next_state['data'], next_state['lengths']
            if action == 'insert_row' and rows < 10:
                position, count = rng.randrange(rows + 1), rng.randint(1, 2)
                command = InsertRowsCommand(self.model, position, count)
                data[position:position] = [[''] * cols for _ in range(count)]
                lengths[position:position] = [cols] * count
            elif action == 'delete_row' and rows:
                position = rng.randrange(rows)
                count = rng.randint(1, rows - position)
                command = DeleteRowsCommand(self.model, position, count, None)
                del data[position:position + count]
                del lengths[position:position + count]
            elif action == 'insert_col' and cols < 10:
                position, count = rng.randrange(cols + 1), rng.randint(1, 2)
                command = InsertColsCommand(self.model, position, count)
                for row in data:
                    row[position:position] = [''] * count
                lengths[:] = [length + count if position <= length else length for length in lengths]
                next_state['width'] += count
            elif action == 'delete_col' and cols:
                position = rng.randrange(cols)
                count = rng.randint(1, cols - position)
                command = DeleteColsCommand(self.model, position, count, None)
                for row in data:
                    del row[position:position + count]
                lengths[:] = [max(0, length - min(count, max(0, length - position))) for length in lengths]
                next_state['width'] -= count
            elif action == 'edit' and rows and cols:
                cells = []
                for _ in range(rng.randint(1, 4)):
                    row, col = rng.randrange(rows), rng.randrange(cols)
                    new = rng.choice(('', 'value', '中文', 'a\r\nb', '"quoted"', '\t')) + str(step)
                    cells.append((row, col, data[row][col], new))
                    data[row][col] = new
                    lengths[row] = max(lengths[row], col + 1)
                command = BatchEditCommand(self.model, cells)
            elif action == 'paste':
                target_rows, target_cols = max(1, rows + rng.randint(0, 2)), max(1, cols + rng.randint(0, 2))
                if target_rows > 12 or target_cols > 12:
                    continue
                for row, length in zip(data, lengths):
                    row.extend([''] * (target_cols - cols))
                lengths[:] = [target_cols if length == cols else length for length in lengths]
                data.extend([[''] * target_cols for _ in range(target_rows - rows)])
                lengths.extend([target_cols] * (target_rows - rows))
                next_state['width'] = target_cols
                cells = []
                for _ in range(rng.randint(1, 4)):
                    row, col = rng.randrange(target_rows), rng.randrange(target_cols)
                    new = 'paste' + str(step)
                    cells.append((row, col, '', new))
                    data[row][col] = new
                    lengths[row] = max(lengths[row], col + 1)
                command = PasteCellsCommand(self.model, cells, target_rows, target_cols)
            else:
                continue
            self.stack.push(command)
            cursor += 1
            history = history[:cursor] + [copy.deepcopy(next_state)]
            state = next_state
            check()
        while cursor:
            self.stack.undo()
            cursor -= 1
            state = copy.deepcopy(history[cursor])
            check()
        while cursor + 1 < len(history):
            self.stack.redo()
            cursor += 1
            state = copy.deepcopy(history[cursor])
            check()
        del tester


if __name__ == '__main__':
    unittest.main()
