"""Boundary checks that exercise GUI actions and native QObject lifetimes."""
import csv
import io
import os
import random
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from PyQt5.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PyQt5.QtGui import QCloseEvent
from PyQt5.QtWidgets import QApplication, QMenu, QMessageBox

from csv_commands import CellEditCommand
from csv_editor import CsvEditorWindow, FindReplaceDialog
from csv_model import CsvFormat
from tests import test_editor as editor_support

APP = editor_support.APP


REAL_LOAD_RECENT = CsvEditorWindow._load_recent
REAL_ADD_RECENT = CsvEditorWindow._add_recent
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class EditorBoundaryTests(unittest.TestCase):
    setUp = editor_support.EditorTests.setUp
    tearDown = editor_support.EditorTests.tearDown
    load = editor_support.EditorTests.load
    current = editor_support.EditorTests.current
    active_editor = editor_support.EditorTests.active_editor
    select_rectangle = editor_support.EditorTests.select_rectangle
    body = editor_support.EditorTests.body
    body_grid = editor_support.EditorTests.body_grid

    def test_clipboard_large_multiline_field_roundtrips_and_restores_limit(self):
        value = '中文\n' + 'long value\t"quoted"' * 9000
        rows = [[value, 'tail']]
        self.load(rows)
        self.select_rectangle(0, 0, 0, 1)
        self.assertTrue(self.window._copy())
        text = QApplication.clipboard().text()
        self.load([['']])
        QApplication.clipboard().setText(text)
        old_limit = csv.field_size_limit()
        with patch('csv_editor.QMessageBox.warning') as warning:
            self.window._paste()
        warning.assert_not_called()
        self.assertEqual(self.body(), rows)
        self.assertEqual(csv.field_size_limit(), old_limit)
        self.window.act_undo.trigger()
        self.assertEqual(self.body(), [['']])

    def test_malformed_clipboard_preserves_document_and_history(self):
        self.load([['old', 'tail']])
        QApplication.clipboard().setText('"unfinished\nfield')
        old_limit = csv.field_size_limit()
        with patch('csv_editor.QMessageBox.warning') as warning:
            self.window.act_paste.trigger()
        warning.assert_called_once()
        self.assertEqual(self.body(), [['old', 'tail']])
        self.assertEqual(self.window.undo_stack.count(), 0)
        self.assertEqual(csv.field_size_limit(), old_limit)

    def test_paste_from_zero_rows_and_zero_columns_undo_redo(self):
        for rows, columns in (([], 0), ([], 3), ([[], []], 0)):
            with self.subTest(rows=rows, columns=columns):
                self.window.model.load_data(rows, column_count=columns)
                self.window.undo_stack.clear()
                QApplication.clipboard().setText('one\ttwo\nthree\tfour')
                self.window.act_paste.trigger()
                self.assertEqual(self.body_grid()[0][:2], ['one', 'two'])
                self.assertEqual(self.body_grid()[1][:2], ['three', 'four'])
                self.window.act_undo.trigger()
                self.assertEqual(self.window.model.get_all_data(), rows)
                self.assertEqual(self.window.model.columnCount(), columns)
                self.window.act_redo.trigger()
                self.assertEqual(self.body_grid()[1][:2], ['three', 'four'])

    def test_empty_grid_copy_cut_delete_find_replace_are_safe(self):
        self.window.model.load_data([], column_count=0)
        self.window.undo_stack.clear()
        QApplication.clipboard().setText('keep clipboard')
        self.assertFalse(self.window._copy())
        for action in (self.window.act_cut, self.window.act_delete,
                       self.window.act_del_row, self.window.act_del_col):
            action.trigger()
        for column_only in (False, True):
            self.window._do_find('text', False, column_only)
            self.window._do_replace('text', 'new', False, column_only)
            self.window._do_replace_all('text', 'new', False, column_only)
        self.assertEqual(QApplication.clipboard().text(), 'keep clipboard')
        self.assertEqual(self.window.model.rowCount(), 0)
        self.assertEqual(self.window.model.columnCount(), 0)
        self.assertEqual(self.window.undo_stack.count(), 0)
        self.assertEqual(self.window.lbl_position.text(), '就绪')

    def test_pending_editor_menu_undo_restores_cell_before_earlier_history(self):
        self.load([['original']])
        self.window.undo_stack.push(CellEditCommand(self.window.model, 1, 0, 'original', 'saved edit'))
        editor = self.active_editor()
        editor.setPlainText('pending edit')
        self.window.act_undo.trigger()
        self.assertIsNone(self.window.delegate.active_editor())
        self.assertEqual(self.body(), [['saved edit']])
        self.assertEqual(self.window.undo_stack.index(), 1)
        self.window.act_undo.trigger()
        self.assertEqual(self.body(), [['original']])
        self.window.act_redo.trigger()
        self.assertEqual(self.body(), [['saved edit']])
        self.window.act_redo.trigger()
        self.assertEqual(self.body(), [['pending edit']])

    def test_pending_editor_menu_undo_enabled_for_clean_document(self):
        self.load([['original']])
        self.assertFalse(self.window.act_undo.isEnabled())
        self.active_editor().setPlainText('pending')
        self.assertTrue(self.window.act_undo.isEnabled())
        self.window.act_undo.trigger()
        self.assertEqual(self.body(), [['original']])
        self.assertTrue(self.window.act_redo.isEnabled())

    def test_failed_undo_keeps_history_dirty_title_and_close_prompt_then_can_retry(self):
        rows = [['a', 'b'], ['c', 'd']]
        self.load(rows)
        self.current(0, 1)
        self.window.act_del_col.trigger()
        command = self.window.undo_stack.command(0)
        deleted = [['a'], ['c']]
        self.assertEqual(self.body(), deleted)
        with patch.object(self.window.model, '_col_letter', side_effect=MemoryError()):
            self.window.act_undo.trigger()
            APP.processEvents()
            self.assertEqual(self.body(), deleted)
            self.assertEqual(self.window.undo_stack.count(), 1)
            self.assertEqual(self.window.undo_stack.index(), 1)
            self.assertIs(self.window.undo_stack.command(0), command)
            self.assertTrue(command.applied)
            self.assertFalse(self.window.undo_stack.isClean())
            self.assertIn('*', self.window.windowTitle())
            with patch('csv_editor.QMessageBox.question', return_value=QMessageBox.Cancel) as question:
                event = QCloseEvent()
                self.window.closeEvent(event)
                self.assertFalse(event.isAccepted())
            question.assert_called_once()
        self.window.act_undo.trigger()
        self.assertEqual(self.body(), rows)
        self.assertEqual(self.window.undo_stack.index(), 0)
        self.assertTrue(self.window.undo_stack.isClean())
        self.assertNotIn('*', self.window.windowTitle())

    def test_failed_redo_keeps_history_and_clean_marker_then_can_retry(self):
        rows = [['a', 'b'], ['c', 'd']]
        self.load(rows)
        self.current(0, 1)
        self.window.act_del_col.trigger()
        self.window.undo_stack.setClean()
        self.window.act_undo.trigger()
        self.assertEqual(self.body(), rows)
        self.assertFalse(self.window.undo_stack.isClean())
        command = self.window.undo_stack.command(0)
        with patch.object(self.window.model, '_col_letter', side_effect=MemoryError()):
            self.window.act_redo.trigger()
            APP.processEvents()
            self.assertEqual(self.body(), rows)
            self.assertEqual(self.window.undo_stack.count(), 1)
            self.assertEqual(self.window.undo_stack.index(), 0)
            self.assertEqual(self.window.undo_stack.cleanIndex(), 1)
            self.assertIs(self.window.undo_stack.command(0), command)
            self.assertFalse(command.applied)
            self.assertFalse(self.window.undo_stack.isClean())
            self.assertIn('*', self.window.windowTitle())
        self.window.act_redo.trigger()
        self.assertEqual(self.body(), [['a'], ['c']])
        self.assertEqual(self.window.undo_stack.index(), 1)
        self.assertTrue(self.window.undo_stack.isClean())
        self.assertNotIn('*', self.window.windowTitle())

    def test_redo_is_disabled_when_pending_editor_would_replace_redo_branch(self):
        self.load([['original']])
        self.window.undo_stack.push(CellEditCommand(self.window.model, 1, 0, 'original', 'first'))
        self.window.act_undo.trigger()
        self.assertTrue(self.window.act_redo.isEnabled())
        self.active_editor().setPlainText('second')
        self.assertFalse(self.window.act_redo.isEnabled())
        self.window.delegate.commit_active_editor()
        self.assertEqual(self.body(), [['second']])
        self.assertFalse(self.window.undo_stack.canRedo())

    def test_sort_attempt_pending_edit_delete_and_undo_keep_file_order(self):
        rows = [['c', 'C'], ['a', 'A'], ['b', 'B']]
        self.load(rows)
        self.window.table.sortByColumn(0, Qt.AscendingOrder)
        self.active_editor(0, 0).setPlainText('z')
        self.window.act_del_row.trigger()
        self.assertEqual(self.body(), [['a', 'A'], ['b', 'B']])
        self.window.act_undo.trigger()
        self.assertEqual(self.body(), [['z', 'C'], ['a', 'A'], ['b', 'B']])
        self.window.act_undo.trigger()
        self.assertEqual(self.body(), rows)

    def test_load_failure_preserves_pending_editor_existing_history_and_format(self):
        self.load([['old']])
        self.window._filepath = 'original.tsv'
        original_format = CsvFormat(delimiter='\t')
        self.window._csv_format = original_format
        self.window.undo_stack.push(CellEditCommand(self.window.model, 1, 0, 'old', 'edited'))
        editor = self.active_editor()
        editor.setPlainText('pending')
        with patch('csv_editor.read_csv_document', side_effect=OSError('missing')), \
                patch('csv_editor.QMessageBox.critical') as error:
            self.assertFalse(self.window._load_file('missing.csv'))
        error.assert_called_once()
        self.assertIs(self.window.delegate.active_editor(), editor)
        self.assertEqual(editor.toPlainText(), 'pending')
        self.assertEqual(self.body(), [['edited']])
        self.assertEqual(self.window.undo_stack.index(), 1)
        self.assertEqual(self.window._filepath, 'original.tsv')
        self.assertIs(self.window._csv_format, original_format)

    def test_replacement_allocation_failure_keeps_pending_editor_and_history(self):
        self.load([['old']])
        self.window.undo_stack.push(CellEditCommand(self.window.model, 1, 0, 'old', 'edited'))
        editor = self.active_editor()
        editor.setPlainText('pending')
        with patch('csv_editor.read_csv_document', return_value=([['new']], CsvFormat())), \
                patch.object(self.window.model, 'load_data', side_effect=MemoryError()), \
                patch('csv_editor.QMessageBox.critical') as error:
            self.assertFalse(self.window._load_file('new.csv'))
        error.assert_called_once()
        self.assertIs(self.window.delegate.active_editor(), editor)
        self.assertEqual(editor.toPlainText(), 'pending')
        self.assertEqual(self.body(), [['edited']])
        self.assertEqual(self.window.undo_stack.index(), 1)

    def test_successful_load_replaces_active_editor_only_after_read_and_prepare(self):
        self.load([['old']])
        self.active_editor().setPlainText('pending')
        with patch('csv_editor.read_csv_document', return_value=([['new']], CsvFormat())):
            self.assertTrue(self.window._load_file('new.csv'))
        APP.processEvents()
        self.assertIsNone(self.window.delegate.active_editor())
        self.assertEqual(self.window.model.get_all_data(), [['new']])
        self.assertEqual(self.window.undo_stack.count(), 0)
        self.assertTrue(self.window.undo_stack.isClean())

    def test_clipboard_allocation_failure_warns_and_preserves_document(self):
        self.load([['old']])
        QApplication.clipboard().setText('new')
        with patch('csv_editor.parse_csv_text', side_effect=MemoryError()), \
                patch('csv_editor.QMessageBox.warning') as warning:
            self.window.act_paste.trigger()
        warning.assert_called_once()
        self.assertEqual(self.body(), [['old']])
        self.assertEqual(self.window.undo_stack.count(), 0)

    def test_model_failure_notice_is_queued_and_preserves_document(self):
        self.load([['old']])
        self.window.model.operationFailed.emit('内存不足')
        self.assertEqual(self.window.status_bar.currentMessage(), '')
        APP.processEvents()
        self.assertEqual(self.window.status_bar.currentMessage(), '操作未完成：内存不足')
        self.assertEqual(self.body(), [['old']])
        self.assertEqual(self.window.undo_stack.count(), 0)

    def test_corrupt_recent_settings_do_not_crash_or_break_successful_save(self):
        self.recent_load.stop()
        self.recent_add.stop()
        for value in (17, True, {}, None, [123, None, {}, '\x00bad'], 'missing.csv'):
            with self.subTest(value=value):
                self.settings_values['recent_files'] = value
                REAL_LOAD_RECENT(self.window)
                self.assertEqual(len(self.window.recent_menu.actions()), 1)
                self.assertFalse(self.window.recent_menu.actions()[0].isEnabled())
                self.window._filepath = 'saved.csv'
                with patch('csv_editor.write_csv') as writer:
                    self.assertTrue(self.window._save_file())
                writer.assert_called_once()
                expected = ['saved.csv', 'missing.csv'] if value == 'missing.csv' else ['saved.csv']
                self.assertEqual(self.settings_values['recent_files'], expected)

    def test_recent_files_handles_legacy_single_path_and_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'file.csv')
            Path(path).write_text('one,two\n', encoding='utf-8')
            self.settings_values['recent_files'] = path
            REAL_LOAD_RECENT(self.window)
            self.assertEqual(self.window.recent_menu.actions()[0].data(), path)
            self.settings_values['recent_files'] = [path, 17, path, '\x00bad']
            REAL_ADD_RECENT(self.window, path)
            self.assertEqual(self.settings_values['recent_files'], [path])
            self.assertEqual(len(self.window.recent_menu.actions()), 1)

    def test_corrupt_bom_settings_defaults_off(self):
        for value in ([False], [], {}, 'false', 12, None, 'maybe', 'yes', '2'):
            with self.subTest(value=value):
                self.settings_values['write_bom'] = value
                self.assertIs(CsvEditorWindow._read_bom_preference(), False)
        with patch('csv_editor.QSettings', side_effect=TypeError('bad native setting')):
            self.assertIs(CsvEditorWindow._read_bom_preference(), False)

    def test_bom_preference_accepts_native_and_legacy_boolean_forms(self):
        for value, expected in ((True, True), (False, False), (1, True), (0, False),
                                (' TRUE ', True), (' false ', False), ('1', True), ('0', False)):
            with self.subTest(value=value):
                self.settings_values['write_bom'] = value
                self.assertIs(CsvEditorWindow._read_bom_preference(), expected)

    def test_find_and_replace_dialogs_reuse_after_close(self):
        for show, attribute in ((self.window._show_find, 'find_dialog'),
                                (self.window._show_replace, 'replace_dialog')):
            show()
            dialog = getattr(self.window, attribute)
            dialog.close()
            show()
            self.assertIs(getattr(self.window, attribute), dialog)
            dialog.close()
        self.assertEqual(len(self.window.findChildren(FindReplaceDialog)), 2)

    def test_deleted_find_dialog_is_recreated_and_status_is_safe(self):
        self.window._show_find()
        old_dialog = self.window.find_dialog
        old_dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.window._set_find_status('safe', '')
        self.window._show_find()
        self.assertIsNot(self.window.find_dialog, old_dialog)
        self.window.find_dialog.close()

    def test_find_wraps_moved_columns_and_no_current_cell(self):
        self.load([['hit left', 'middle', 'hit right'], ['last hit', 'plain', 'tail']])
        self.window.table.horizontalHeader().moveSection(2, 0)
        self.window.table.setCurrentIndex(self.window.proxy.index(-1, -1))
        self.window._do_find('hit', True, False)
        self.assertEqual((self.window.table.currentIndex().row(), self.window.table.currentIndex().column()),
                         (0, 2))
        self.window._do_find('hit', True, False)
        self.assertEqual((self.window.table.currentIndex().row(), self.window.table.currentIndex().column()),
                         (0, 0))
        self.window._do_find('hit', True, False)
        self.assertEqual(self.window.table.currentIndex().row(), 1)
        self.window._do_find('hit', True, False)
        self.assertEqual((self.window.table.currentIndex().row(), self.window.table.currentIndex().column()),
                         (0, 2))

    def test_context_menu_dismissals_release_owned_actions(self):
        baseline = len(self.window.findChildren(QMenu))
        with patch('csv_editor.QMenu.exec_', return_value=None):
            for _ in range(6):
                self.window._show_context_menu(QPoint(0, 0))
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                self.assertEqual(len(self.window.findChildren(QMenu)), baseline)

    def test_seeded_sort_attempts_and_moved_column_actions_roundtrip_history(self):
        rng = random.Random(872)
        values = ['', '中文', 'line\nline', '"quoted"', 'tab\tvalue', 'alpha']
        for iteration in range(70):
            rows = [[rng.choice(values) for _ in range(rng.randrange(1, 5))]
                    for _ in range(rng.randrange(1, 6))]
            with self.subTest(iteration=iteration):
                self.load(rows)
                columns = self.window.model.columnCount()
                header = self.window.table.horizontalHeader()
                for logical in range(columns):
                    header.moveSection(header.visualIndex(logical), logical)
                header.moveSection(rng.randrange(columns), 0)
                self.window.table.sortByColumn(rng.randrange(columns), rng.choice((Qt.AscendingOrder, Qt.DescendingOrder)))
                row = rng.randrange(self.window.proxy.rowCount())
                column = rng.randrange(columns)
                self.current(row, column)
                before = self.body()
                before_columns = self.window.model.columnCount()
                operation = rng.randrange(6)
                if operation == 0:
                    self.window.act_ins_row.trigger()
                elif operation == 1:
                    self.window.act_ins_col.trigger()
                elif operation == 2:
                    self.window.act_del_row.trigger()
                elif operation == 3:
                    self.window.act_del_col.trigger()
                elif operation == 4:
                    buffer = io.StringIO(newline='')
                    csv.writer(buffer, delimiter='\t').writerows(
                        [[rng.choice(values) for _ in range(rng.randrange(1, 4))]
                         for _ in range(rng.randrange(1, 4))])
                    QApplication.clipboard().setText(buffer.getvalue())
                    self.window.act_paste.trigger()
                else:
                    self.window._do_replace_all('a', 'replacement', False, False)
                after = self.body()
                after_columns = self.window.model.columnCount()
                if self.window.undo_stack.count():
                    self.window.act_undo.trigger()
                    self.assertEqual(self.body(), before)
                    self.assertEqual(self.window.model.columnCount(), before_columns)
                    self.window.act_redo.trigger()
                    self.assertEqual(self.body(), after)
                    self.assertEqual(self.window.model.columnCount(), after_columns)
class NativeGuiBoundaryTests(unittest.TestCase):
    def run_child(self, operation):
        code = textwrap.dedent('''
            import os
            os.environ['QT_QPA_PLATFORM'] = 'offscreen'
            import faulthandler
            faulthandler.enable()
            faulthandler.dump_traceback_later(12, exit=True)
            from unittest.mock import MagicMock, patch
            from PyQt5.QtCore import QCoreApplication, QEvent, Qt
            from PyQt5.QtWidgets import QApplication
            from csv_editor import CsvEditorWindow
            app = QApplication([])
            settings = MagicMock()
            settings.value.side_effect = lambda key, default=None, **kwargs: default
            with patch('csv_editor.QSettings', return_value=settings):
                window = CsvEditorWindow()
                window.show()
                app.processEvents()
                window.model.load_data([['c', 'C'], ['a', 'A'], ['b', 'B']])
                window.undo_stack.clear()
                window.table.setCurrentIndex(window.proxy.index(0, 0))
                window.table.edit(window.table.currentIndex())
                app.processEvents()
                editor = window.delegate.active_editor()
                assert editor is not None
                editor.setPlainText('changed')
        ''')
        code += textwrap.indent(textwrap.dedent(operation), '    ')
        code += textwrap.dedent('''
                window.undo_stack.clear()
                window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                app.processEvents()
                print('GUI boundary OK')
        ''')
        result = subprocess.run([sys.executable, '-c', code], cwd=PROJECT_ROOT,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('GUI boundary OK', result.stdout)

    def test_closed_editor_deletion_save_action_and_pending_timer_do_not_abort(self):
        self.run_child('''
            window.delegate.closeEditor.emit(editor, window.delegate.RevertModelCache)
            editor.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
            assert window.delegate.active_editor() is None
            assert not window.delegate.has_pending_changes()
            window._filepath = 'saved.csv'
            with patch('csv_editor.write_csv') as writer:
                window.act_save.trigger()
                writer.assert_called_once()
            assert window.model.get_all_data() == [['c', 'C'], ['a', 'A'], ['b', 'B']]
        ''')

    def test_model_reset_cancels_editor_timer_without_native_abort(self):
        self.run_child('''
            window.model.load_data([['reset']])
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
            assert window.delegate.active_editor() is None
            assert window.model.get_all_data() == [['reset']]
            window._update_title()
        ''')

    def test_sort_attempt_and_menu_undo_during_edit_do_not_abort(self):
        self.run_child('''
            window.table.sortByColumn(0, Qt.AscendingOrder)
            app.processEvents()
            window.act_undo.trigger()
            app.processEvents()
            assert window.delegate.active_editor() is None
            assert window.model.get_all_data() == [['c', 'C'], ['a', 'A'], ['b', 'B']]
            window.act_redo.trigger()
            app.processEvents()
            assert window.model.get_all_data()[1][0] == 'changed'
        ''')


if __name__ == '__main__':
    unittest.main()
