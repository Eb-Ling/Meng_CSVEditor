"""Selection sharing, conflict protection, local transport and client config."""
import copy
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PyQt5.QtCore import QModelIndex, QPersistentModelIndex, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from csv_commands import DeleteColsCommand, InsertRowsCommand
from mcp_config import client_configs, merge_claude_config, server_launch
from region_store import RegionClient, RegionError, RegionStore, region_page, value_hash
from tests import test_editor as editor_support

APP = editor_support.APP


class RegionTests(unittest.TestCase):
    setUpEditor = editor_support.EditorTests.setUp
    tearDownEditor = editor_support.EditorTests.tearDown
    load = editor_support.EditorTests.load
    current = editor_support.EditorTests.current
    select_rectangle = editor_support.EditorTests.select_rectangle
    active_editor = editor_support.EditorTests.active_editor

    def setUp(self):
        self.setUpEditor()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.bridge = self.window.region_bridge
        self.bridge.store = RegionStore(self.directory.name)
        self.client = RegionClient(self.directory.name)
        self.load([['name', 'number'], ['中文\n"quoted"\tdata', '001'], ['tail', '002']])

    def tearDown(self):
        self.bridge.shutdown()
        self.tearDownEditor()

    def publish(self, first_row=0, first_col=0, last_row=1, last_col=1):
        self.select_rectangle(first_row, first_col, last_row, last_col)
        self.window._show_region_details()
        self.assertTrue(self.window.region_panel.submit())
        return self.bridge.read_snapshot()

    def edit(self, snapshot, cell=0, value='new'):
        old = snapshot['cells'][cell]
        return {'cell_id': old['cell_id'], 'expected_sha256': old['sha256'], 'value': value}

    def network(self, operation):
        result = {}
        def worker():
            try:
                result['value'] = operation()
            except Exception as error:
                result['error'] = error
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        deadline = time.monotonic() + 12
        while thread.is_alive() and time.monotonic() < deadline:
            APP.processEvents()
            thread.join(0.01)
        self.assertFalse(thread.is_alive(), 'Local bridge did not finish')
        if 'error' in result:
            raise result['error']
        return result['value']

    def test_viewing_region_is_private_until_submit_and_full_detail_visible(self):
        self.select_rectangle(1, 0, 2, 1)
        self.window._show_region_details()
        panel = self.window.region_panel
        self.assertTrue(panel.isVisible())
        self.assertEqual(panel.cells.topLevelItemCount(), 4)
        self.assertEqual(panel.detail.toPlainText(), '中文\n"quoted"\tdata')
        self.assertIsNone(self.bridge.server)
        self.assertFalse(self.bridge.store.path.exists())

    def test_readonly_panel_shortcuts_do_not_mutate_the_grid(self):
        self.select_rectangle(1, 0, 1, 1)
        self.window._show_region_details()
        original = self.window.model.get_all_data()
        detail = self.window.region_panel.detail
        detail.setFocus()
        detail.selectAll()
        QTest.keyClick(detail, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(QApplication.clipboard().text(), '中文\n"quoted"\tdata')
        QApplication.clipboard().setText('unexpected grid paste')
        for key, modifiers in ((Qt.Key_V, Qt.ControlModifier), (Qt.Key_X, Qt.ControlModifier),
                               (Qt.Key_Delete, Qt.NoModifier), (Qt.Key_D, Qt.ControlModifier),
                               (Qt.Key_I, Qt.ControlModifier), (Qt.Key_J, Qt.ControlModifier)):
            QTest.keyClick(detail, key, modifiers)
        self.assertEqual(self.window.model.get_all_data(), original)
        self.assertTrue(self.window.undo_stack.isClean())

    def test_collapsed_panel_reopens_and_keeps_selection(self):
        self.select_rectangle(0, 0, 1, 1)
        self.window._show_region_details()
        panel = self.window.region_panel
        panel.toggle_collapsed()
        APP.processEvents()
        self.assertTrue(panel.collapsed)
        self.assertFalse(panel.body.isVisible())
        self.assertLessEqual(panel.width(), 42)
        panel.toggle_collapsed()
        APP.processEvents()
        self.assertTrue(panel.body.isVisible())
        self.assertEqual(panel.cells.topLevelItemCount(), 4)

    def test_oversized_selection_is_rejected_before_materializing_all_indexes(self):
        self.load([['value'] * 51 for _ in range(100)])
        self.select_rectangle(0, 0, 99, 50)
        with patch.object(self.window.table.selectionModel(), 'selectedIndexes',
                          side_effect=AssertionError('Should not allocate the huge selection')):
            self.window._show_region_details()
        self.assertIsNone(self.bridge.server)
        self.assertIn('5000', self.window.status_bar.currentMessage())

    def test_submission_contains_only_selected_cells_and_basename(self):
        self.window._filepath = Path('C:/private/location/table.csv')
        snapshot = self.publish(1, 0, 1, 1)
        self.assertEqual([cell['value'] for cell in snapshot['cells']], ['中文\n"quoted"\tdata', '001'])
        self.assertEqual(snapshot['document'], 'table.csv')
        self.assertNotIn('private', json.dumps(snapshot))
        self.assertNotIn('token', snapshot)
        self.assertEqual(self.bridge.server.server_address[0], '127.0.0.1')

    def test_submit_commits_pending_cell(self):
        self.select_rectangle(1, 0, 1, 1)
        self.window._show_region_details()
        editor = self.active_editor(1, 1)
        editor.setPlainText('003')
        self.assertTrue(self.window.region_panel.submit())
        self.assertEqual(self.bridge.snapshot['cells'][1]['value'], '003')
        self.assertIsNone(self.window.delegate.active_editor())

    def test_sort_attempt_moved_selection_keeps_source_identity(self):
        self.window.table.sortByColumn(1, Qt.DescendingOrder)
        header = self.window.table.horizontalHeader()
        header.moveSection(1, 0)
        snapshot = self.publish(1, 0, 1, 1)
        cells = snapshot['cells']
        self.assertEqual(cells[0]['column'], 2)
        expected_row = cells[0]['row'] - 1
        result = self.bridge.apply_edits(snapshot['region_id'], 0, [self.edit(snapshot, 0, '999')])
        self.assertEqual(result['changed_cells'], 1)
        self.assertEqual(self.window.model._data[expected_row][1], '999')

    def test_ai_batch_is_one_undo_step_and_does_not_save(self):
        snapshot = self.publish()
        original = self.window.model.get_all_data()
        old_count = self.window.undo_stack.count()
        result = self.bridge.apply_edits(snapshot['region_id'], 0,
                                         [self.edit(snapshot, 0, 'title'), self.edit(snapshot, 3, '007')])
        self.assertEqual(result['changed_cells'], 2)
        self.assertFalse(result['saved_to_csv'])
        self.assertFalse(self.window.undo_stack.isClean())
        self.assertEqual(self.window.undo_stack.count(), old_count + 1)
        self.window._undo()
        self.assertEqual(self.window.model.get_all_data(), original)
        self.window._redo()
        self.assertEqual(self.window.model._data[2][1], '007')

    def test_conflict_rejects_entire_batch_and_pending_edit_is_preserved(self):
        snapshot = self.publish()
        editor = self.active_editor(1, 1)
        editor.setPlainText('manual')
        with self.assertRaisesRegex(RegionError, '内容已改变'):
            self.bridge.apply_edits(snapshot['region_id'], 0,
                                    [self.edit(snapshot, 0, 'wrong'), self.edit(snapshot, 3, 'overwrite')])
        self.assertEqual(self.window.model._data[1][0], 'name')
        self.assertEqual(self.window.model._data[2][1], 'manual')

    def test_invalid_cell_or_duplicate_never_partly_changes_data(self):
        snapshot = self.publish()
        original = self.window.model.get_all_data()
        valid = self.edit(snapshot)
        for edits in ([valid, {**valid, 'cell_id': 'outside'}], [valid, valid],
                      [{**valid, 'value': 1}], [{**valid, 'expected_sha256': 'wrong'}],
                      [{**valid, 'extra': 'no'}], [], None):
            with self.subTest(edits=edits):
                with self.assertRaises(RegionError):
                    self.bridge.apply_edits(snapshot['region_id'], 0, edits)
                self.assertEqual(self.window.model.get_all_data(), original)

    def test_resubmit_and_revision_reject_old_requests(self):
        first = self.publish()
        second = self.publish(2, 0, 2, 1)
        with self.assertRaises(RegionError):
            self.bridge.apply_edits(first['region_id'], 0, [self.edit(first)])
        self.bridge.apply_edits(second['region_id'], 0, [self.edit(second)])
        with self.assertRaises(RegionError):
            self.bridge.apply_edits(second['region_id'], 0, [self.edit(second)])
        with self.assertRaises(RegionError):
            self.bridge.apply_edits(second['region_id'], True, [self.edit(second)])

    def test_readonly_switch_revokes_writes(self):
        snapshot = self.publish()
        self.window.region_panel.allow_edits.setChecked(False)
        current = self.bridge.read_snapshot()
        self.assertFalse(current['editable'])
        with self.assertRaisesRegex(RegionError, '仅允许阅读'):
            self.bridge.apply_edits(current['region_id'], current['revision'], [self.edit(current)])

    def test_readonly_switch_is_effective_even_when_cache_write_fails(self):
        snapshot = self.publish()
        with patch.object(self.bridge.store, 'write', side_effect=OSError('disk full')):
            self.window.region_panel.allow_edits.setChecked(False)
        current = self.bridge.read_snapshot()
        self.assertFalse(current['editable'])
        with self.assertRaisesRegex(RegionError, '仅允许阅读'):
            self.bridge.apply_edits(current['region_id'], current['revision'], [self.edit(current)])

    def test_model_reset_and_deleted_cells_disable_old_writes(self):
        snapshot = self.publish()
        self.window.model.load_data([['other', 'document']])
        self.assertFalse(self.bridge.read_snapshot()['editable'])
        with self.assertRaises(RegionError):
            self.bridge.apply_edits(snapshot['region_id'], 0, [self.edit(snapshot)])
        self.load([['a', 'b']])
        snapshot = self.publish(0, 0, 0, 1)
        self.window.undo_stack.push(DeleteColsCommand(self.window.model, 0, 1, None))
        APP.processEvents()
        self.assertFalse(self.window.region_panel.submit_button.isEnabled())
        with self.assertRaises(RegionError):
            self.bridge.apply_edits(snapshot['region_id'], 0, [self.edit(snapshot, 1)])

    def test_row_insertion_tracks_submitted_cells(self):
        snapshot = self.publish(1, 0, 1, 1)
        self.window.undo_stack.push(InsertRowsCommand(self.window.model, 0))
        self.assertEqual(self.bridge.read_snapshot()['cells'][0]['current_row'], 4)
        self.bridge.apply_edits(snapshot['region_id'], 0, [self.edit(snapshot, 1, '008')])
        self.assertEqual(self.window.model._data[3][1], '008')

    def test_failed_command_preserves_data_history_and_snapshot(self):
        snapshot = self.publish()
        original = self.window.model.get_all_data()
        with patch.object(self.window.model, '_apply_cells', return_value=False):
            with self.assertRaises(RegionError):
                self.bridge.apply_edits(snapshot['region_id'], 0, [self.edit(snapshot)])
        self.assertEqual(self.window.model.get_all_data(), original)
        self.assertEqual(self.bridge.snapshot['revision'], 0)
        self.assertTrue(self.window.undo_stack.isClean())

    def test_initial_store_failure_does_not_publish_or_modify_document(self):
        self.select_rectangle(0, 0, 1, 1)
        self.window._show_region_details()
        with patch.object(self.bridge.store, 'write', side_effect=OSError('disk full')):
            self.assertFalse(self.window.region_panel.submit())
        self.assertIsNone(self.bridge.snapshot)
        self.assertFalse(self.bridge.store.path.exists())
        self.assertTrue(self.window.undo_stack.isClean())

    def test_apply_store_failure_reports_applied_changes_truthfully(self):
        snapshot = self.publish()
        with patch.object(self.bridge.store, 'write', side_effect=OSError('disk full')):
            result = self.bridge.apply_edits(snapshot['region_id'], 0, [self.edit(snapshot)])
        self.assertEqual(result['changed_cells'], 1)
        self.assertIsNotNone(result['persistence_warning'])
        self.assertEqual(self.window.model._data[1][0], 'new')
        self.assertFalse(self.window.undo_stack.isClean())

    def test_local_transport_reads_applies_and_persists_offline_snapshot(self):
        snapshot = self.publish()
        fetched = self.network(self.client.snapshot)
        self.assertEqual(fetched['region_id'], snapshot['region_id'])
        self.assertNotIn('connection', fetched)
        result = self.network(lambda: self.client.apply(snapshot['region_id'], 0, [self.edit(snapshot)]))
        self.assertEqual(result['changed_cells'], 1)
        self.bridge.shutdown()
        offline = self.client.snapshot()
        self.assertFalse(offline['editable'])
        self.assertEqual(offline['cells'][0]['value'], 'new')
        with self.assertRaises(RegionError):
            self.client.apply(offline['region_id'], offline['revision'], [self.edit(offline)])

    def test_auth_origin_host_and_oversized_requests_are_rejected(self):
        snapshot = self.publish()
        record = self.bridge.store.read()
        endpoint = record['connection']
        def send(extra):
            connection = http.client.HTTPConnection('127.0.0.1', endpoint['port'], timeout=3)
            try:
                headers = {'Authorization': 'Bearer ' + endpoint['token'], 'Content-Type': 'application/json'}
                headers.update(extra)
                connection.request('POST', '/rpc', body=b'{}', headers=headers)
                response = connection.getresponse(); response.read()
                return response.status
            finally:
                connection.close()
        for header in ({'Authorization': 'wrong'}, {'Origin': 'https://untrusted.example'},
                       {'Host': 'untrusted.example'}, {'Content-Type': 'text/plain'},
                       {'Content-Length': '999999999'}):
            with self.subTest(header=list(header)):
                self.assertIn(self.network(lambda: send(header)), (400, 403))
        self.assertEqual(self.bridge.snapshot['revision'], 0)

    def test_another_submission_owner_disables_old_bridge(self):
        snapshot = self.publish()
        record = self.bridge.store.read()
        record['session_id'] = 'another-window'
        self.bridge.store.write(record)
        with self.assertRaisesRegex(RegionError, '另一个窗口'):
            self.bridge.apply_edits(snapshot['region_id'], 0, [self.edit(snapshot)])


class RegionUtilityTests(unittest.TestCase):
    def test_page_preserves_hash_and_marks_long_text_for_full_read(self):
        value = 'long\n' * 600
        snapshot = {'region_id': 'r', 'revision': 0, 'cells': [
            {'cell_id': 'c', 'value': value, 'sha256': value_hash(value)}]}
        page = region_page(snapshot, 0, 1)
        self.assertEqual(page['cells'][0]['value'], value[:2000])
        self.assertTrue(page['cells'][0]['value_truncated'])
        self.assertEqual(page['cells'][0]['sha256'], value_hash(value))
        for offset, limit in ((-1, 1), (True, 1), (0, 0), (0, 501)):
            with self.assertRaises(RegionError):
                region_page(snapshot, offset, limit)

    def test_configurations_parse_and_keep_other_claude_settings(self):
        try:
            import tomllib
        except ImportError:
            tomllib = None
        configs = client_configs('D:/CSV Tool/Meng_CSVEditor_MCP.exe', [])
        if tomllib is not None:
            self.assertEqual(tomllib.loads(configs['Codex'])['mcp_servers']['meng_csv_editor']['command'],
                             'D:/CSV Tool/Meng_CSVEditor_MCP.exe')
        else:
            self.assertIn('command = "D:/CSV Tool/Meng_CSVEditor_MCP.exe"', configs['Codex'])
        self.assertEqual(json.loads(configs['Claude Desktop']), json.loads(configs['通用 MCP 客户端']))
        old = {'theme': 'dark', 'mcpServers': {'existing': {'command': 'existing.exe'}}}
        new = merge_claude_config(old, 'csv.exe', [])
        self.assertEqual(new['theme'], old['theme'])
        self.assertIn('existing', new['mcpServers'])
        self.assertNotIn('meng_csv_editor', old['mcpServers'])
        command, args = server_launch('D:/toolproduct/Meng_CSVEditor-main/dist', frozen=True)
        self.assertTrue(command.endswith('/Meng_CSVEditor_MCP.exe'))
        self.assertEqual(args, [])

    def test_corrupt_store_is_a_clear_error_and_bad_endpoint_is_never_contacted(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RegionStore(directory)
            store.path.write_text('{broken', encoding='utf-8')
            with self.assertRaises(RegionError):
                store.read()
            record = {'schema_version': 1, 'snapshot': {'region_id': 'r', 'revision': 0, 'cells': []},
                      'connection': {'host': 'external.example', 'port': 80, 'token': 'x' * 32}}
            store.write(record)
            with self.assertRaises(RegionError):
                RegionClient(directory).snapshot()


if __name__ == '__main__':
    unittest.main()
