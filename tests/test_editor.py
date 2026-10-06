"""Regression checks for file safety, visible table edits, and editor shortcuts."""
import csv
import codecs
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QCoreApplication, QEvent, QItemSelection, QItemSelectionModel, Qt, QTimer
from PyQt5.QtGui import QCloseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialogButtonBox, QMessageBox

from csv_commands import CellEditCommand
from csv_editor import CsvEditorWindow, SaveSettingsDialog
from csv_model import CsvFormat
from ui_theme import apply_theme


APP = QApplication.instance() or QApplication([])
apply_theme(APP)


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.settings_values = {}
        self.settings = MagicMock()
        self.settings.value.side_effect = lambda key, default=None, **kwargs: self.settings_values.get(key, default)
        self.settings.setValue.side_effect = lambda key, value: self.settings_values.__setitem__(key, value)
        self.settings_patch = patch('csv_editor.QSettings', return_value=self.settings)
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.recent_load = patch.object(CsvEditorWindow, '_load_recent')
        self.recent_add = patch.object(CsvEditorWindow, '_add_recent')
        self.recent_load.start()
        self.recent_add.start()
        self.addCleanup(self.recent_load.stop)
        self.addCleanup(self.recent_add.stop)
        self.window = CsvEditorWindow()
        self.window.show()
        self.window.activateWindow()
        APP.processEvents()

    def tearDown(self):
        editor = self.window.delegate._active_editor
        if editor is not None:
            self.window.delegate.closeEditor.emit(editor, self.window.delegate.RevertModelCache)
        self.window.undo_stack.clear()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        QApplication.clipboard().clear()

    def load(self, rows):
        self.window.delegate.commit_active_editor()
        # These fixtures describe body rows; the file has a real heading record.
        width = max((len(row) for row in rows), default=0)
        self.window.model.load_data([[f'字段{column + 1}' for column in range(width)], *rows])
        self.window.undo_stack.clear()
        APP.processEvents()

    def body(self):
        return self.window.model.get_all_data()[1:]

    def body_grid(self):
        return self.window.model._data[1:]

    def current(self, row, column):
        index = self.window.proxy.index(row, column)
        self.window.table.setCurrentIndex(index)
        return index

    def select_rectangle(self, first_row, first_col, last_row, last_col):
        selection = QItemSelection(self.window.proxy.index(first_row, first_col),
                                   self.window.proxy.index(last_row, last_col))
        self.window.table.selectionModel().select(selection, QItemSelectionModel.ClearAndSelect)

    def active_editor(self, row=0, column=0):
        self.window.table.edit(self.current(row, column))
        APP.processEvents()
        editor = self.window.delegate._active_editor
        self.assertIsNotNone(editor)
        editor.setFocus()
        APP.processEvents()
        return editor

    def make_dirty(self):
        self.window.undo_stack.push(CellEditCommand(self.window.model, 1, 0, '', 'unsaved'))

    def test_save_as_cancel_blocks_discard(self):
        self.make_dirty()
        with patch('csv_editor.QMessageBox.question', return_value=QMessageBox.Save), \
                patch('csv_editor.QFileDialog.getSaveFileName', return_value=('', '')):
            self.assertFalse(self.window._confirm_discard())
        self.assertFalse(self.window.undo_stack.isClean())
        self.assertIsNone(self.window._filepath)
        self.assertEqual(self.body_grid()[0][0], 'unsaved')

    def test_failed_save_blocks_close_and_preserves_path(self):
        self.make_dirty()
        self.window._filepath = 'original.csv'
        original_format = self.window._csv_format
        event = QCloseEvent()
        with patch('csv_editor.write_csv', side_effect=OSError('disk full')), \
                patch('csv_editor.QMessageBox.critical'), \
                patch('csv_editor.QMessageBox.question', return_value=QMessageBox.Save):
            self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        with patch('csv_editor.QFileDialog.getSaveFileName', return_value=('new.tsv', '')), \
                patch('csv_editor.write_csv', side_effect=OSError('disk full')), \
                patch('csv_editor.QMessageBox.critical'):
            self.assertFalse(self.window._save_as())
        self.assertEqual(self.window._filepath, 'original.csv')
        self.assertEqual(self.window._csv_format, original_format)
        self.assertFalse(self.window.undo_stack.isClean())

    def test_ctrl_s_commits_pending_editor_before_write(self):
        self.load([['old']])
        editor = self.active_editor()
        editor.setPlainText('latest\nline')
        self.assertIn('*', self.window.windowTitle())
        self.window._filepath = 'saved.csv'
        with patch('csv_editor.write_csv') as writer:
            QTest.keyClick(editor, Qt.Key_S, Qt.ControlModifier)
            APP.processEvents()
        writer.assert_called_once()
        self.assertEqual(writer.call_args.args[1], [['字段1'], ['latest\nline']])
        self.assertEqual(self.body_grid(), [['latest\nline']])
        self.assertTrue(self.window.undo_stack.isClean())
        self.assertIsNone(self.window.delegate._active_editor)

    def test_pending_editor_is_included_in_discard_prompt(self):
        self.load([['original']])
        self.active_editor().setPlainText('changed')
        with patch('csv_editor.QMessageBox.question', return_value=QMessageBox.Cancel) as question:
            self.assertFalse(self.window._confirm_discard())
        question.assert_called_once()
        self.assertEqual(self.body_grid(), [['changed']])
        self.assertFalse(self.window.undo_stack.isClean())

    def test_opening_crlf_cell_editor_does_not_change_data(self):
        original = 'first\r\nsecond'
        self.load([[original]])
        self.active_editor()
        self.assertFalse(self.window.delegate.has_pending_changes())
        self.window.delegate.commit_active_editor()
        self.assertEqual(self.body_grid(), [[original]])
        self.assertTrue(self.window.undo_stack.isClean())
        self.assertEqual(self.window.undo_stack.count(), 0)

    def test_delete_column_then_undo_restores_all_rows(self):
        rows = [['A1', 'B1', 'C1'], ['A2', 'B2', 'C2'], ['A3', 'B3', 'C3']]
        self.load(rows)
        self.current(0, 1)
        self.window._delete_col()
        self.assertEqual(self.body_grid(), [['A1', 'C1'], ['A2', 'C2'], ['A3', 'C3']])
        self.window.undo_stack.undo()
        self.assertEqual(self.body_grid(), rows)
        self.window.undo_stack.redo()
        self.assertEqual(self.window.model.columnCount(), 2)
        self.assertIn('4 行 x 2 列', self.window.lbl_size.text())

    def test_delete_last_row_then_undo_keeps_column_width(self):
        rows = [['left', 'middle', 'right']]
        self.load(rows)
        self.current(0, 0)
        self.window._delete_row()
        self.window.undo_stack.undo()
        self.assertEqual(self.body_grid(), rows)
        self.assertEqual(self.window.model.columnCount(), 3)

    def test_paste_growth_is_one_complete_undo_step(self):
        self.load([['original']])
        self.current(0, 0)
        QApplication.clipboard().setText('a\tb\tc\r\nd\te\tf\r\ng\th\ti\r\n')
        self.window._paste()
        self.assertEqual(self.body_grid(), [['a', 'b', 'c'], ['d', 'e', 'f'], ['g', 'h', 'i']])
        self.assertEqual(self.window.undo_stack.count(), 1)
        self.window.undo_stack.undo()
        self.assertEqual(self.body_grid(), [['original']])
        self.assertEqual(self.window.model.columnCount(), 1)
        self.assertTrue(self.window.undo_stack.isClean())
        self.assertIn('2 行 x 1 列', self.window.lbl_size.text())
        self.window.undo_stack.redo()
        self.assertEqual(self.body_grid()[-1], ['g', 'h', 'i'])

    def test_sort_attempt_does_not_reorder_copied_rows(self):
        self.load([['c', 'C'], ['a', 'A'], ['b', 'B']])
        self.window.table.sortByColumn(0, Qt.AscendingOrder)
        self.select_rectangle(0, 0, 1, 1)
        self.window._copy()
        block = list(csv.reader(io.StringIO(QApplication.clipboard().text()), delimiter='\t'))
        self.assertEqual(block, [['c', 'C'], ['a', 'A']])

    def test_sort_attempt_does_not_move_paste_targets(self):
        rows = [['c', 'C'], ['a', 'A'], ['b', 'B']]
        self.load(rows)
        self.window.table.sortByColumn(0, Qt.AscendingOrder)
        self.current(0, 0)
        QApplication.clipboard().setText('z\tZ\r\ny\tY\r\n')
        self.window._paste()
        self.assertEqual(self.body_grid(), [['z', 'Z'], ['y', 'Y'], ['b', 'B']])
        self.window.undo_stack.undo()
        self.assertEqual(self.body_grid(), rows)

    def test_copy_paste_roundtrips_multiline_tabs_and_quotes(self):
        rows = [['one\ttwo', 'line1\r\nline2', 'say "hello"'], ['中文', '', 'tail']]
        self.load(rows)
        self.select_rectangle(0, 0, 1, 2)
        self.window._copy()
        self.load([['']])
        self.current(0, 0)
        self.window._paste()
        self.assertEqual(self.body_grid(), rows)

    def test_copy_and_paste_follow_moved_column_order(self):
        self.load([['left', 'middle', 'right']])
        self.window.table.horizontalHeader().moveSection(2, 0)
        self.select_rectangle(0, 0, 0, 2)
        self.window._copy()
        block = list(csv.reader(io.StringIO(QApplication.clipboard().text()), delimiter='\t'))
        self.assertEqual(block, [['right', 'left', 'middle']])
        self.current(0, 2)
        QApplication.clipboard().setText('R\tL\tM')
        self.window._paste()
        self.assertEqual(self.body_grid(), [['L', 'M', 'R']])

    def test_find_current_column_advances_in_file_order_despite_sort_attempt(self):
        self.load([['c', 'hit C'], ['a', 'hit A'], ['b', 'hit B']])
        self.window.table.sortByColumn(0, Qt.AscendingOrder)
        self.current(0, 1)
        self.window._do_find('hit', True, True)
        self.assertEqual(self.window.table.currentIndex().row(), 1)
        self.window._do_find('hit', True, True)
        self.assertEqual(self.window.table.currentIndex().row(), 2)
        self.window._do_find('hit', True, True)
        self.assertEqual(self.window.table.currentIndex().row(), 0)

    def test_replace_all_current_column_is_dirty_and_single_undo(self):
        rows = [['cat', 'CAT'], ['catcat', 'cat']]
        self.load(rows)
        self.current(0, 1)
        self.window._do_replace_all('cat', r'\g<1>', False, True)
        self.assertEqual(self.body_grid(), [['cat', r'\g<1>'], ['catcat', r'\g<1>']])
        self.assertFalse(self.window.undo_stack.isClean())
        self.assertEqual(self.window.undo_stack.count(), 1)
        self.window.undo_stack.undo()
        self.assertEqual(self.body_grid(), rows)
        self.assertTrue(self.window.undo_stack.isClean())

    def test_empty_replace_is_noop_and_noop_replace_keeps_clean(self):
        self.load([['text']])
        self.current(0, 0)
        self.window._do_replace('', 'new', False, False)
        self.window._do_replace_all('', 'new', False, False)
        self.window._do_replace_all('text', 'text', True, False)
        self.assertEqual(self.body_grid(), [['text']])
        self.assertEqual(self.window.undo_stack.count(), 0)
        self.assertTrue(self.window.undo_stack.isClean())

    def test_editor_shortcuts_edit_text_without_changing_grid(self):
        self.load([['original']])
        editor = self.active_editor()
        editor.selectAll()
        QTest.keyClick(editor, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(QApplication.clipboard().text(), 'original')
        QTest.keyClick(editor, Qt.Key_Delete)
        self.assertEqual(editor.toPlainText(), '')
        QApplication.clipboard().setText('pasted\ntext')
        QTest.keyClick(editor, Qt.Key_V, Qt.ControlModifier)
        self.assertEqual(editor.toPlainText(), 'pasted\ntext')
        self.assertEqual(self.body_grid(), [['original']])
        self.assertEqual(self.window.undo_stack.count(), 0)
        QTest.keyClick(editor, Qt.Key_Escape)
        APP.processEvents()
        self.assertEqual(self.body_grid(), [['original']])

    def test_long_editor_stays_inside_viewport_and_escape_restores_row(self):
        self.load([['a', 'b'], ['c', 'd']])
        self.window.resize(380, 230)
        APP.processEvents()
        original_height = self.window.table.rowHeight(1)
        editor = self.active_editor(1, 1)
        editor.setPlainText('\n'.join(['very long content ' * 10] * 80))
        APP.processEvents()
        APP.processEvents()
        self.assertTrue(self.window.table.viewport().rect().contains(editor.geometry()))
        self.assertGreater(editor.verticalScrollBar().maximum(), 0)
        QTest.keyClick(editor, Qt.Key_Escape)
        APP.processEvents()
        self.assertEqual(self.window.table.rowHeight(1), original_height)
        self.assertEqual(self.window.undo_stack.count(), 0)

    def test_load_save_preserves_tsv_encoding_and_newlines(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.tsv'
            original = b'\xef\xbb\xbfhead\tvalue\nrow\t"one\ntwo"\n'
            source.write_bytes(original)
            self.assertTrue(self.window._load_file(str(source)))
            self.assertIn('utf-8', self.window.lbl_size.text())
            self.assertIn('TSV', self.window.lbl_size.text())
            self.assertTrue(self.window._save_file())
            self.assertEqual(source.read_bytes(), original[3:])
            self.assertEqual(self.window._encoding, 'utf-8')
            self.assertEqual(self.window._csv_format.bom, b'')

    def test_save_as_from_bom_document_writes_no_bom(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.csv'
            destination = Path(directory) / 'saved.tsv'
            source.write_bytes(b'\xef\xbb\xbfhead,value\nrow,data\n')
            self.assertTrue(self.window._load_file(str(source)))
            with patch('csv_editor.QFileDialog.getSaveFileName', return_value=(str(destination), '')):
                self.assertTrue(self.window._save_as())
            self.assertEqual(destination.read_bytes(), b'head\tvalue\nrow\tdata\n')
            self.assertTrue(source.read_bytes().startswith(b'\xef\xbb\xbf'))
            self.assertEqual(self.window._encoding, 'utf-8')
            self.assertEqual(self.window._csv_format.bom, b'')

    def test_saving_utf16_document_updates_encoding_after_success(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.csv'
            source.write_bytes('名称,数值\n中文,1\n'.encode('utf-16'))
            self.assertTrue(self.window._load_file(str(source)))
            self.assertTrue(self.window._save_file())
            self.assertEqual(source.read_bytes(), '名称,数值\n中文,1\n'.encode('utf-8'))
            self.assertEqual(self.window._encoding, 'utf-8')
            self.assertIn('utf-8', self.window.lbl_size.text())

    def test_save_as_tsv_uses_tab_and_success_updates_title(self):
        self.window.model.load_data([['left', 'right']])
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'saved.tsv'
            with patch('csv_editor.QFileDialog.getSaveFileName', return_value=(str(destination), '')):
                self.assertTrue(self.window._save_as())
            self.assertEqual(destination.read_bytes(), b'left\tright\r\n')
            self.assertIn('saved.tsv', self.window.windowTitle())
            self.assertIn('TSV', self.window.lbl_size.text())

    def test_bom_preference_defaults_off_without_writing_settings(self):
        self.assertFalse(self.window._write_bom)
        self.assertIn('BOM 关', self.window.lbl_size.text())
        self.settings.value.assert_called_once_with('write_bom', False)
        self.settings.setValue.assert_not_called()

    def test_settings_cancel_does_not_change_or_persist_preference(self):
        def cancel_dialog():
            dialog = APP.activeModalWidget()
            dialog.bom_checkbox.setChecked(True)
            QTest.mouseClick(dialog.button_box.button(QDialogButtonBox.Cancel), Qt.LeftButton)

        QTimer.singleShot(0, cancel_dialog)
        self.window._show_save_settings()
        self.assertFalse(self.window._write_bom)
        self.assertIn('BOM 关', self.window.lbl_size.text())
        self.settings.setValue.assert_not_called()

    def test_settings_ok_persists_and_new_window_restores_preference(self):
        initial_states = []

        def accept_dialog():
            dialog = APP.activeModalWidget()
            initial_states.append(dialog.bom_checkbox.isChecked())
            dialog.bom_checkbox.setChecked(True)
            QTest.mouseClick(dialog.button_box.button(QDialogButtonBox.Ok), Qt.LeftButton)

        QTimer.singleShot(0, accept_dialog)
        self.window.act_save_settings.trigger()
        self.assertEqual(initial_states, [False])
        self.assertTrue(self.window._write_bom)
        self.assertIn('BOM 开', self.window.lbl_size.text())
        self.settings.setValue.assert_called_once_with('write_bom', True)
        restarted = CsvEditorWindow()
        try:
            self.assertTrue(restarted._write_bom)
            self.assertIn('BOM 开', restarted.lbl_size.text())
            dialog = SaveSettingsDialog(restarted._write_bom, restarted)
            self.assertTrue(dialog.bom_checkbox.isChecked())
            dialog.deleteLater()
        finally:
            restarted.close()
            restarted.deleteLater()

    def test_bom_enabled_save_and_save_as_emit_exactly_one_bom(self):
        self.window.model.load_data([['head', 'value'], ['row', 'data']])
        self.window._set_bom_preference(True)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'saved.csv'
            destination = Path(directory) / 'saved.tsv'
            self.window._filepath = str(source)
            self.assertTrue(self.window._save_file())
            expected = codecs.BOM_UTF8 + b'head,value\r\nrow,data\r\n'
            self.assertEqual(source.read_bytes(), expected)
            self.assertTrue(self.window._save_file())
            self.assertEqual(source.read_bytes(), expected)
            with patch('csv_editor.QFileDialog.getSaveFileName', return_value=(str(destination), '')):
                self.assertTrue(self.window._save_as())
            self.assertEqual(destination.read_bytes(), codecs.BOM_UTF8 + b'head\tvalue\r\nrow\tdata\r\n')
            self.assertEqual(self.window._csv_format.bom, codecs.BOM_UTF8)

    def test_disabling_bom_removes_existing_bom_on_next_save(self):
        self.window.model.load_data([['text']])
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'saved.csv'
            self.window._filepath = str(destination)
            self.window._set_bom_preference(True)
            self.assertTrue(self.window._save_file())
            self.assertEqual(destination.read_bytes(), codecs.BOM_UTF8 + b'text\r\n')
            self.window._set_bom_preference(False)
            self.assertTrue(self.window._save_file())
            self.assertEqual(destination.read_bytes(), b'text\r\n')
            self.assertEqual(self.window._csv_format.bom, b'')

    def test_bom_setting_keeps_gbk_output_without_bom(self):
        self.window.model.load_data([['名称', '数值']])
        self.window._csv_format = CsvFormat(encoding='gbk')
        self.window._set_bom_preference(True)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'saved.csv'
            self.window._filepath = str(destination)
            self.assertTrue(self.window._save_file())
            self.assertEqual(destination.read_bytes(), '名称,数值\r\n'.encode('gbk'))
            self.assertEqual(self.window._csv_format.bom, b'')


if __name__ == '__main__':
    unittest.main()
