"""CSV record 1 is a heading record; edits retain file order and coordinates."""
import csv
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PyQt5.QtCore import QPoint, Qt, QTimer
from PyQt5.QtWidgets import QApplication, QInputDialog

from region_store import RegionError, RegionStore
from tests import test_editor as support

APP = support.APP


class HeaderTests(unittest.TestCase):
    setUp = support.EditorTests.setUp
    tearDownEditor = support.EditorTests.tearDown
    current = support.EditorTests.current
    active_editor = support.EditorTests.active_editor
    select_rectangle = support.EditorTests.select_rectangle

    def tearDown(self):
        self.window.region_bridge.shutdown()
        self.tearDownEditor()

    def load_csv(self, rows, columns=None):
        self.window.model.load_data(rows, column_count=columns)
        self.window.undo_stack.clear()
        APP.processEvents()

    def heading(self, column, role=Qt.DisplayRole):
        return self.window.proxy.headerData(column, Qt.Horizontal, role)

    def edit_heading(self, column, value, accepted=True):
        with patch('csv_editor.QInputDialog.getMultiLineText', return_value=(value, accepted)):
            self.window._edit_header(column)
        APP.processEvents()

    def test_first_csv_record_is_shown_once_as_headings_not_body(self):
        rows = [['编号', '说明'], ['001', '甲'], ['002', '乙']]
        self.load_csv(rows)
        self.assertEqual(self.window.model.get_all_data(), rows)
        self.assertEqual(self.window.proxy.rowCount(), 2)
        self.assertEqual(self.heading(0), '编号')
        self.assertEqual(self.heading(1), '说明')
        self.assertEqual(self.window.proxy.index(0, 0).data(), '001')
        self.assertEqual(self.window.proxy.mapToSource(self.window.proxy.index(0, 0)).row(), 1)
        self.assertEqual(self.window.proxy.headerData(0, Qt.Vertical), '2')
        self.assertIn('3 行 x 2 列（含抬头）', self.window.lbl_size.text())

    def test_right_click_menu_edits_logical_column_after_moving_it(self):
        self.load_csv([['编号', '名称'], ['001', '甲']])
        header = self.window.table.horizontalHeader()
        header.moveSection(1, 0)
        point = QPoint(header.sectionViewportPosition(1) + 10, 10)
        def activate(menu, position):
            self.assertIn('编辑抬头', menu.actions()[0].text())
            self.assertFalse(any('排序' in action.text() for action in menu.actions()))
            menu.actions()[0].trigger()
        with patch('csv_editor.QMenu.exec_', activate), \
                patch('csv_editor.QInputDialog.getMultiLineText', return_value=('物品名称', True)):
            self.window._show_header_menu(point)
        self.assertEqual(self.window.model.get_all_data()[0], ['编号', '物品名称'])
        self.assertEqual(self.heading(1), '物品名称')

    def test_heading_edit_is_dirty_undoable_and_saved_as_first_record_without_bom(self):
        rows = [['编号', '说明'], ['001', '保持不变']]
        self.load_csv(rows)
        self.edit_heading(1, '说明,"多行"\n第二行\tTab')
        self.assertEqual(self.window.undo_stack.count(), 1)
        self.assertFalse(self.window.undo_stack.isClean())
        self.assertIn('*', self.window.windowTitle())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'saved.csv'
            self.window._filepath = str(path)
            self.assertTrue(self.window._save_file())
            self.assertFalse(path.read_bytes().startswith(b'\xef\xbb\xbf'))
            with path.open(encoding='utf-8', newline='') as stream:
                self.assertEqual(list(csv.reader(stream)), [
                    ['编号', '说明,"多行"\n第二行\tTab'], rows[1]])
        self.window._undo()
        self.assertEqual(self.window.model.get_all_data(), rows)
        self.assertEqual(self.heading(1), '说明')
        self.window._redo()
        self.assertEqual(self.heading(1, Qt.EditRole), '说明,"多行"\n第二行\tTab')

    def test_real_multiline_dialog_accept_and_cancel(self):
        self.load_csv([['原名'], ['001']])
        def accept():
            dialog = APP.activeModalWidget()
            self.assertIsInstance(dialog, QInputDialog)
            dialog.setTextValue('中文\n新抬头')
            dialog.accept()
        QTimer.singleShot(0, accept)
        self.window._edit_header(0)
        self.assertEqual(self.heading(0, Qt.EditRole), '中文\n新抬头')
        before = self.window.model.get_all_data()
        QTimer.singleShot(0, lambda: APP.activeModalWidget().reject())
        self.window._edit_header(0)
        self.assertEqual(self.window.model.get_all_data(), before)
        self.assertEqual(self.window.undo_stack.count(), 1)

    def test_cancel_and_unchanged_heading_do_not_create_history(self):
        rows = [['名称'], ['甲']]
        self.load_csv(rows)
        self.edit_heading(0, '未接受', False)
        self.edit_heading(0, '名称')
        self.assertEqual(self.window.model.get_all_data(), rows)
        self.assertTrue(self.window.undo_stack.isClean())
        self.assertEqual(self.window.undo_stack.count(), 0)

    def test_blank_duplicate_and_short_heading_records_preserve_actual_values(self):
        rows = [[''], ['甲', '乙', '丙']]
        self.load_csv(rows)
        self.assertEqual(self.heading(0), '（空抬头）')
        self.assertEqual(self.heading(1, Qt.EditRole), '')
        self.assertEqual(self.window.model.get_all_data(), rows)
        self.edit_heading(2, '重复')
        self.edit_heading(1, '重复')
        self.assertEqual(self.heading(1), self.heading(2))
        self.window._undo()
        self.window._undo()
        self.assertEqual(self.window.model.get_all_data(), rows)

    def test_header_only_file_can_edit_insert_body_paste_and_undo(self):
        self.load_csv([['名称', '数量']])
        self.assertEqual(self.window.proxy.rowCount(), 0)
        self.edit_heading(1, '库存')
        self.window._insert_row_above()
        self.assertEqual(self.window.proxy.rowCount(), 1)
        self.assertEqual(self.window.model.get_all_data(), [['名称', '库存'], ['', '']])
        self.current(0, 0)
        QApplication.clipboard().setText('甲\t2\n乙\t3')
        self.window._paste()
        self.assertEqual(self.window.model.get_all_data(), [['名称', '库存'], ['甲', '2'], ['乙', '3']])
        self.window._undo()
        self.window._undo()
        self.window._undo()
        self.assertEqual(self.window.model.get_all_data(), [['名称', '数量']])

    def test_empty_file_view_and_cancel_do_not_add_a_record(self):
        self.load_csv([], columns=1)
        self.assertEqual(self.window.proxy.rowCount(), 0)
        self.edit_heading(0, '未接受', False)
        self.assertEqual(self.window.model.get_all_data(), [])
        self.edit_heading(0, '名称')
        self.assertEqual(self.window.model.get_all_data(), [['名称']])
        self.assertEqual(self.window.proxy.rowCount(), 0)
        self.window._undo()
        self.assertEqual(self.window.model.get_all_data(), [])

    def test_insert_body_into_completely_empty_grid_is_one_undo_step(self):
        self.load_csv([], columns=0)
        self.window._insert_row_below()
        self.assertEqual(self.window.model.get_all_data(), [[''], ['']])
        self.assertEqual(self.window.proxy.rowCount(), 1)
        self.assertEqual(self.window.undo_stack.count(), 1)
        self.window._undo()
        self.assertEqual(self.window.model.get_all_data(), [])
        self.assertEqual(self.window.model.columnCount(), 0)

    def test_delete_last_body_row_keeps_heading_and_undo_restores_body(self):
        self.load_csv([['编号'], ['001']])
        self.current(0, 0)
        self.window._delete_row()
        self.assertEqual(self.window.model.get_all_data(), [['编号']])
        self.assertEqual(self.window.proxy.rowCount(), 0)
        self.window._undo()
        self.assertEqual(self.window.model.get_all_data(), [['编号'], ['001']])

    def test_insert_delete_columns_include_heading_values_and_undo(self):
        rows = [['编号', '说明'], ['001', '甲']]
        self.load_csv(rows)
        self.current(0, 1)
        self.window._insert_col_left()
        self.assertEqual(self.window.model.get_all_data(), [['编号', '', '说明'], ['001', '', '甲']])
        self.assertEqual(self.heading(2), '说明')
        self.window._undo()
        self.current(0, 0)
        self.window._delete_col()
        self.assertEqual(self.heading(0), '说明')
        self.assertEqual(self.window.model.get_all_data(), [['说明'], ['甲']])
        self.window._undo()
        self.assertEqual(self.window.model.get_all_data(), rows)

    def test_header_click_and_direct_sort_calls_cannot_reorder_rows(self):
        rows = [['排序测试', '值'], ['c', '3'], ['a', '1'], ['b', '2']]
        self.load_csv(rows)
        self.assertFalse(self.window.table.isSortingEnabled())
        self.assertFalse(self.window.table.horizontalHeader().isSortIndicatorShown())
        for column in (0, 1):
            for order in (Qt.AscendingOrder, Qt.DescendingOrder):
                self.window.table.horizontalHeader().sectionClicked.emit(column)
                self.window.table.sortByColumn(column, order)
                self.window.proxy.sort(column, order)
                self.assertEqual([self.window.proxy.index(row, 0).data() for row in range(3)], ['c', 'a', 'b'])
                self.assertEqual(self.window.model.get_all_data(), rows)
        self.active_editor(0, 0).setPlainText('z')
        self.window.delegate.commit_active_editor()
        self.assertEqual([self.window.proxy.index(row, 0).data() for row in range(3)], ['z', 'a', 'b'])

    def test_body_copy_replace_and_paste_do_not_change_headings(self):
        self.load_csv([['cat', 'cat'], ['cat', '001']])
        self.select_rectangle(0, 0, 0, 1)
        self.window._copy()
        self.assertEqual(list(csv.reader(io.StringIO(QApplication.clipboard().text()), delimiter='\t')),
                         [['cat', '001']])
        self.window._do_replace_all('cat', 'dog', True, False)
        self.assertEqual(self.window.model.get_all_data(), [['cat', 'cat'], ['dog', '001']])
        self.current(0, 0)
        QApplication.clipboard().setText('甲\t2\n乙\t3')
        self.window._paste()
        self.assertEqual(self.window.model.get_all_data(), [['cat', 'cat'], ['甲', '2'], ['乙', '3']])

    def test_pending_body_editor_commits_before_heading_edit_and_undo_is_ordered(self):
        self.load_csv([['名称'], ['甲']])
        self.active_editor().setPlainText('乙')
        self.edit_heading(0, '新名称')
        self.assertEqual(self.window.model.get_all_data(), [['新名称'], ['乙']])
        self.assertEqual(self.window.undo_stack.count(), 2)
        self.window._undo()
        self.assertEqual(self.window.model.get_all_data(), [['名称'], ['乙']])
        self.window._undo()
        self.assertEqual(self.window.model.get_all_data(), [['名称'], ['甲']])

    def test_heading_changed_while_dialog_open_is_not_overwritten(self):
        self.load_csv([['名称'], ['甲']])
        def concurrent(*args):
            self.window.model.setData(self.window.model.index(0, 0), '其他修改')
            return '过期输入', True
        with patch('csv_editor.QInputDialog.getMultiLineText', side_effect=concurrent), \
                patch('csv_editor.QMessageBox.warning') as warning:
            self.window._edit_header(0)
        warning.assert_called_once()
        self.assertEqual(self.heading(0), '其他修改')
        self.assertEqual(self.window.undo_stack.count(), 0)

    def test_document_switched_while_dialog_open_does_not_receive_old_heading(self):
        self.load_csv([['名称'], ['甲']])
        def switched(*args):
            self.window.model.load_data([['另一抬头'], ['乙']])
            return '过期输入', True
        with patch('csv_editor.QInputDialog.getMultiLineText', side_effect=switched), \
                patch('csv_editor.QMessageBox.warning') as warning:
            self.window._edit_header(0)
        warning.assert_called_once()
        self.assertEqual(self.window.model.get_all_data(), [['另一抬头'], ['乙']])

    def test_failed_heading_command_preserves_heading_and_history(self):
        self.load_csv([['名称'], ['甲']])
        with patch.object(self.window.model, '_apply_cells', return_value=False):
            self.edit_heading(0, '失败输入')
        self.assertEqual(self.heading(0), '名称')
        self.assertTrue(self.window.undo_stack.isClean())
        self.assertEqual(self.window.undo_stack.count(), 0)

    def test_heading_mcp_submission_edit_undo_and_conflict(self):
        self.load_csv([['名称', '编号'], ['甲', '001']])
        with tempfile.TemporaryDirectory() as directory:
            bridge = self.window.region_bridge
            bridge.store = RegionStore(directory)
            self.window._show_header_details(1)
            self.assertEqual(self.window.region_panel.detail.toPlainText(), '编号')
            self.assertTrue(self.window.region_panel.submit())
            snapshot = bridge.read_snapshot()
            cell = snapshot['cells'][0]
            self.assertEqual(cell['address'], 'B1')
            self.assertEqual(cell['display_row'], 1)
            edit = dict(cell_id=cell['cell_id'], expected_sha256=cell['sha256'], value='物品编号')
            result = bridge.apply_edits(snapshot['region_id'], 0, [edit])
            self.assertEqual(result['changed_cells'], 1)
            self.assertEqual(self.heading(1), '物品编号')
            self.assertEqual(self.window.model.get_all_data()[1], ['甲', '001'])
            self.window._undo()
            self.assertEqual(self.heading(1), '编号')
            self.assertTrue(bridge.read_snapshot()['cells'][0]['changed_since_submission'])
            with self.assertRaises(RegionError):
                bridge.apply_edits(snapshot['region_id'], 1, [edit])
            bridge.shutdown()

    def test_body_mcp_addresses_use_file_record_numbers(self):
        self.load_csv([['名称'], ['甲']])
        with tempfile.TemporaryDirectory() as directory:
            bridge = self.window.region_bridge
            bridge.store = RegionStore(directory)
            self.select_rectangle(0, 0, 0, 0)
            self.window._show_region_details()
            self.assertIn('A2:A2', self.window.region_panel.summary.text())
            self.assertTrue(self.window.region_panel.submit())
            cell = bridge.read_snapshot()['cells'][0]
            self.assertEqual(cell['address'], 'A2')
            self.assertEqual(cell['display_row'], 2)
            bridge.shutdown()


if __name__ == '__main__':
    unittest.main()
