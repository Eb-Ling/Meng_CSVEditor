import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5 import sip
from PyQt5.QtTest import QSignalSpy
from PyQt5.QtWidgets import QApplication, QWidget

from csv_commands import (CellEditCommand, DeleteColsCommand, InsertRowsCommand,
                          PasteCellsCommand, SafeUndoStack)
from csv_model import CsvTableModel


class SafeUndoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.model = CsvTableModel()
        self.model.load_data([['a', 'b']])
        self.stack = SafeUndoStack()

    def state(self):
        return (self.model.get_all_data(), self.model.columnCount(), self.stack.count(),
                self.stack.index(), self.stack.cleanIndex(), self.stack.isClean(),
                self.stack.canUndo(), self.stack.canRedo())

    def test_failed_undo_retains_unsaved_deletion_history_and_retry(self):
        self.stack.push(DeleteColsCommand(self.model, 1, 1, None))
        before = self.state()
        clean_changes = QSignalSpy(self.stack.cleanChanged)
        index_changes = QSignalSpy(self.stack.indexChanged)
        failures = QSignalSpy(self.model.operationFailed)
        with patch.object(self.model, '_col_letter', side_effect=MemoryError('allocation')):
            self.assertFalse(self.stack.undo())
        self.assertEqual(self.state(), before)
        self.assertFalse(self.stack.isClean())
        self.assertEqual((len(clean_changes), len(index_changes)), (0, 0))
        self.assertEqual((len(failures), self.model._operation_failure_serial), (1, 1))
        self.assertTrue(self.stack.undo())
        self.assertEqual(self.model.get_all_data(), [['a', 'b']])
        self.assertEqual((self.stack.count(), self.stack.index(), self.stack.isClean()), (1, 0, True))
        self.assertTrue(self.stack.redo())
        self.assertEqual(self.model.get_all_data(), [['a']])
        self.assertFalse(self.stack.isClean())

    def test_failed_redo_retains_history_and_original_clean_marker(self):
        self.stack.push(PasteCellsCommand(self.model, [(1, 2, '', 'new')], 2, 3))
        self.stack.setClean()
        self.stack.undo()
        before = self.state()
        changes = QSignalSpy(self.stack.cleanChanged)
        with patch.object(self.model, '_col_letter', side_effect=MemoryError('allocation')):
            self.assertFalse(self.stack.redo())
        self.assertEqual(self.state(), before)
        self.assertFalse(self.stack.isClean())
        self.assertEqual(len(changes), 0)
        self.assertTrue(self.stack.redo())
        self.assertTrue(self.stack.isClean())
        self.assertEqual((self.stack.count(), self.stack.index(), self.stack.cleanIndex()), (1, 1, 1))
        self.assertEqual(self.model.get_all_data()[1], ['', '', 'new'])

    def test_failed_redo_from_initial_clean_document_stays_clean_and_retryable(self):
        self.stack.push(InsertRowsCommand(self.model, 1))
        self.stack.undo()
        before = self.state()
        with patch.object(self.model, 'insertRows', return_value=False):
            self.assertFalse(self.stack.redo())
        self.assertEqual(self.state(), before)
        self.assertTrue(self.stack.isClean())
        self.assertTrue(self.stack.canRedo())
        self.assertTrue(self.stack.redo())
        self.assertFalse(self.stack.isClean())

    def test_failed_new_push_preserves_existing_redo_branch(self):
        self.stack.push(CellEditCommand(self.model, 0, 0, 'a', 'first'))
        self.stack.push(CellEditCommand(self.model, 0, 0, 'first', 'second'))
        self.stack.undo()
        before = self.state()
        with patch.object(self.model, 'insertRows', side_effect=MemoryError('allocation')):
            self.stack.push(InsertRowsCommand(self.model, 1))
        self.assertEqual(self.state(), before)
        self.stack.push(InsertRowsCommand(self.model, -1))
        self.assertEqual(self.state(), before)
        self.assertTrue(self.stack.redo())
        self.assertEqual(self.model.get_all_data()[0][0], 'second')

    def test_successful_new_push_replaces_redo_branch_once(self):
        self.stack.push(CellEditCommand(self.model, 0, 0, 'a', 'first'))
        self.stack.push(CellEditCommand(self.model, 0, 0, 'first', 'second'))
        self.stack.undo()
        self.stack.push(InsertRowsCommand(self.model, 1))
        self.assertEqual((self.model.rowCount(), self.stack.count(), self.stack.index()), (2, 2, 2))
        self.assertFalse(self.stack.canRedo())
        self.stack.undo()
        self.assertEqual(self.model.rowCount(), 1)

    def test_set_index_stops_on_failure_and_publishes_only_final_state(self):
        for old, new in (('a', 'first'), ('first', 'second'), ('second', 'third')):
            self.stack.push(CellEditCommand(self.model, 0, 0, old, new))
        command = self.stack.command(1)
        changes = QSignalSpy(self.stack.indexChanged)
        with patch.object(command, '_undo_cells', return_value=False):
            self.stack.setIndex(0)
        self.assertEqual((self.stack.index(), self.model.get_all_data()[0][0]), (2, 'second'))
        self.assertEqual([change[0] for change in changes], [2])
        self.stack.setIndex(0)
        self.assertEqual(self.model.get_all_data(), [['a', 'b']])
        with patch.object(command, '_redo_cells', return_value=False):
            self.stack.setIndex(3)
        self.assertEqual((self.stack.index(), self.model.get_all_data()[0][0]), (1, 'first'))
        self.stack.setIndex(3)
        self.assertEqual((self.stack.index(), self.model.get_all_data()[0][0]), (3, 'third'))

    def test_action_trigger_uses_safe_steps_and_deleted_action_is_harmless(self):
        parent = QWidget()
        self.stack.push(DeleteColsCommand(self.model, 1, 1, None))
        action = self.stack.createUndoAction(parent, '撤销')
        before = self.state()
        with patch.object(self.model, '_col_letter', side_effect=MemoryError('allocation')):
            action.trigger()
        self.assertEqual(self.state(), before)
        action.trigger()
        self.assertTrue(self.stack.isClean())
        redo = self.stack.createRedoAction(parent, '重做')
        redo.trigger()
        self.assertFalse(self.stack.isClean())
        sip.delete(action)
        sip.delete(redo)
        self.stack.undo()
        self.assertTrue(self.stack.isClean())

    def test_failure_state_survives_native_qt_callbacks_in_subprocess(self):
        code = textwrap.dedent('''
            from unittest.mock import patch
            from PyQt5.QtCore import QTimer
            from PyQt5.QtWidgets import QApplication
            from csv_model import CsvTableModel
            from csv_commands import DeleteColsCommand, SafeUndoStack
            app = QApplication([])
            model = CsvTableModel()
            model.load_data([['a', 'b']])
            stack = SafeUndoStack()
            stack.push(DeleteColsCommand(model, 1, 1, None))
            def verify():
                with patch.object(model, '_col_letter', side_effect=MemoryError('allocation')):
                    stack.undo()
                assert model.get_all_data() == [['a']]
                assert (stack.count(), stack.index(), stack.isClean()) == (1, 1, False)
                stack.undo()
                assert model.get_all_data() == [['a', 'b']]
                assert stack.isClean()
                print('history survived')
                app.quit()
            QTimer.singleShot(0, verify)
            app.exec_()
        ''')
        result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('history survived', result.stdout)


if __name__ == '__main__':
    unittest.main()
