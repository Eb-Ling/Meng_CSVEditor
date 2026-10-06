"""Marshal local agent requests onto Qt's UI thread and undo history."""
import copy
from datetime import datetime, timezone
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
import threading
import time
import uuid

from PyQt5 import sip
from PyQt5.QtCore import QObject, QPersistentModelIndex, Qt, pyqtSignal

from csv_commands import BatchEditCommand
from csv_model import CsvTableModel
from region_store import MAX_CELLS, MAX_TEXT_BYTES, MAX_RECORD_BYTES, RegionError, RegionStore, value_hash


class RegionBridge(QObject):
    changed = pyqtSignal()
    accessed = pyqtSignal(str)
    rpc_requested = pyqtSignal(object)

    def __init__(self, window, directory=None):
        super().__init__(window)
        self.window = window
        self.store = RegionStore(directory)
        self.session_id = uuid.uuid4().hex
        self.snapshot = None
        self.bindings = {}
        self.server = None
        self._token = secrets.token_urlsafe(32)
        self._closed = False
        self.rpc_requested.connect(self._dispatch, Qt.QueuedConnection)
        window.model.modelAboutToBeReset.connect(self._invalidate)

    def _record(self, snapshot=None, connected=True):
        return {'schema_version': 1, 'session_id': self.session_id,
                'snapshot': snapshot if snapshot is not None else self.snapshot,
                'connection': ({'host': '127.0.0.1', 'port': self.server.server_port, 'token': self._token}
                               if connected and self.server else None)}

    def _latest(self):
        record = self.store.read()
        return (record is not None and record.get('session_id') == self.session_id
                and self.snapshot is not None
                and record['snapshot']['region_id'] == self.snapshot['region_id'])

    def submit(self, selected, allow_edits=True):
        if not selected:
            raise RegionError('请先选中需要查看的单元格。')
        if len(selected) > MAX_CELLS:
            raise RegionError(f'一次最多提交 {MAX_CELLS} 个单元格，请选择较小的区域。')
        cells, bindings, seen = [], {}, set()
        text_size = 0
        for index, display_row, display_column in selected:
            if (not index.isValid() or index.model() is not self.window.model
                    or index.row() >= self.window.model.rowCount() or index.column() >= self.window.model.columnCount()):
                raise RegionError('区域中的单元格已删除，请重新选择。')
            position = (index.row(), index.column())
            if position in seen:
                continue
            seen.add(position)
            value = str(index.data(Qt.EditRole) or '')
            text_size += len(value.encode('utf-8'))
            if text_size > MAX_TEXT_BYTES:
                raise RegionError('区域内容超过 4 MiB，请选择较小的区域。')
            cell_id = f'cell-{len(cells) + 1}'
            cells.append({'cell_id': cell_id, 'row': index.row() + 1, 'column': index.column() + 1,
                          'address': CsvTableModel._col_letter(index.column()) + str(index.row() + 1),
                          'display_row': display_row + 1, 'display_column': display_column + 1,
                          'value': value, 'sha256': value_hash(value)})
            bindings[cell_id] = QPersistentModelIndex(index)
        snapshot = {'region_id': uuid.uuid4().hex, 'revision': 0,
                    'submitted_at': datetime.now(timezone.utc).isoformat(),
                    'document': os.path.basename(os.fspath(self.window._filepath))
                                if self.window._filepath else '未命名文档',
                    'encoding': self.window._encoding, 'delimiter': self.window._csv_format.delimiter,
                    'allow_edits': bool(allow_edits), 'document_generation': self.window.model._document_generation,
                    'cells': cells}
        self._start_server()
        with self.store.locked():
            self.store.write(self._record(snapshot))
        self.snapshot, self.bindings = snapshot, bindings
        self.changed.emit()
        return self.read_snapshot()

    def _binding_valid(self):
        return (not self._closed and self.snapshot is not None
                and bool(self.bindings) and len(self.bindings) == len(self.snapshot['cells'])
                and self.snapshot['document_generation'] == self.window.model._document_generation
                and all(index.isValid() for index in self.bindings.values()))

    def read_snapshot(self):
        if self.snapshot is None:
            raise RegionError('还没有提交区域。')
        result = copy.deepcopy(self.snapshot)
        valid = self._binding_valid()
        result.update(live_available=valid, editable=valid and result['allow_edits'])
        if not valid:
            result['unavailable_reason'] = '原文档或单元格已变化，修改前请重新提交区域。'
        if valid:
            for cell in result['cells']:
                index = self.bindings[cell['cell_id']]
                cell.update(current_row=index.row() + 1, current_column=index.column() + 1,
                            current_address=CsvTableModel._col_letter(index.column()) + str(index.row() + 1),
                            changed_since_submission=value_hash(str(index.data(Qt.EditRole) or '')) != cell['sha256'])
        return result

    def set_edit_allowed(self, allowed):
        if self.snapshot is None:
            return
        with self.store.locked():
            if not self._latest():
                return
            candidate = copy.deepcopy(self.snapshot)
            candidate['allow_edits'] = bool(allowed)
            candidate['revision'] += 1
            self.snapshot = candidate
            try:
                self.store.write(self._record(candidate))
            except OSError:
                self.changed.emit()
                raise RegionError('权限已在当前窗口生效，但缓存写入失败，请重新提交区域。') from None
        self.changed.emit()

    def apply_edits(self, region_id, revision, edits):
        if self.snapshot is None or region_id != self.snapshot['region_id']:
            raise RegionError('区域已重新提交，请读取最新区域。')
        if type(revision) is not int or revision != self.snapshot['revision']:
            raise RegionError('区域版本已变化，请读取最新区域后再修改。')
        if not self._binding_valid():
            raise RegionError('原文档或单元格已变化，请重新提交区域。')
        if not self.snapshot['allow_edits']:
            raise RegionError('当前区域仅允许阅读。请在侧栏开启“允许 AI 修改此区域”。')
        if not isinstance(edits, list) or not edits or len(edits) > MAX_CELLS:
            raise RegionError('edits 必须是非空修改列表，且不超过 5000 项。')
        # Commit pending text first; its new hash must pass the same conflict
        # check as a normal manual edit, rather than being overwritten by AI.
        self.window.delegate.commit_active_editor()
        original = {cell['cell_id']: cell for cell in self.snapshot['cells']}
        candidate = copy.deepcopy(self.snapshot)
        proposed = {cell['cell_id']: cell for cell in candidate['cells']}
        changes, targets = [], set()
        for edit in edits:
            if not isinstance(edit, dict) or set(edit) != {'cell_id', 'expected_sha256', 'value'}:
                raise RegionError('每项修改需要 cell_id、expected_sha256 和 value。')
            cell_id, expected, value = edit['cell_id'], edit['expected_sha256'], edit['value']
            if not isinstance(cell_id, str) or cell_id not in original or cell_id in targets:
                raise RegionError('只能修改已提交区域中不重复的 cell_id。')
            if not isinstance(value, str) or not isinstance(expected, str):
                raise RegionError('单元格内容和 expected_sha256 必须是字符串。')
            targets.add(cell_id)
            index = self.bindings[cell_id]
            current = str(index.data(Qt.EditRole) or '')
            if expected != original[cell_id]['sha256'] or value_hash(current) != expected:
                raise RegionError(f'{original[cell_id]["address"]} 的内容已改变，请重新提交区域，避免覆盖手工修改。')
            if current != value:
                changes.append((index.row(), index.column(), current, value))
            proposed[cell_id].update(value=value, sha256=value_hash(value))
        if sum(len(cell['value'].encode('utf-8')) for cell in candidate['cells']) > MAX_TEXT_BYTES:
            raise RegionError('修改后的区域超过 4 MiB，请缩小操作。')
        if not changes:
            return {'region_id': region_id, 'revision': revision, 'changed_cells': 0}
        candidate['revision'] += 1
        command = BatchEditCommand(self.window.model, changes, 'AI 修改提交区域')
        with self.store.locked():
            if not self._latest():
                raise RegionError('另一个窗口已提交新区域，请读取最新区域。')
            self.window._push_command(command)
            if not command.applied:
                raise RegionError('修改未能完成，原表格保持不变。')
            self.snapshot = candidate
            warning = None
            try:
                self.store.write(self._record())
            except OSError:
                # The document remains the source of truth: do not report a
                # successful model change as a failed/no-change transaction.
                warning = '修改已应用并可撤销，但区域缓存暂未写入；请保持编辑器打开并重新提交。'
        self.changed.emit()
        self.accessed.emit(f'AI 已修改 {len(changes)} 个单元格，可按 Ctrl+Z 撤销')
        return {'region_id': region_id, 'revision': candidate['revision'], 'changed_cells': len(changes),
                'saved_to_csv': False, 'persistence_warning': warning}

    def _invalidate(self):
        self.bindings = {}
        self.changed.emit()

    def _dispatch(self, request):
        if request['cancelled'].is_set() or self._closed:
            request['event'].set()
            return
        try:
            arguments = request['arguments']
            if not self._latest():
                raise RegionError('区域已更新，请读取最后一次提交的区域。')
            if arguments.get('region_id') != self.snapshot['region_id']:
                raise RegionError('区域已更新，请重新读取。')
            if request['method'] == 'read':
                result = self.read_snapshot()
                self.accessed.emit('AI 已读取最后提交的区域')
            elif request['method'] == 'apply':
                result = self.apply_edits(arguments.get('region_id'), arguments.get('revision'), arguments.get('edits'))
            else:
                raise RegionError('未知区域操作。')
            request['response'] = {'ok': True, 'result': result}
        except Exception as error:
            request['response'] = {'ok': False, 'error': str(error)}
        finally:
            request['event'].set()

    def _start_server(self):
        if self.server is not None:
            return
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _reply(self, status, payload):
                data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Connection', 'close')
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    pass

            def do_POST(self):
                self.connection.settimeout(5)
                if (self.path != '/rpc' or self.headers.get('Origin') is not None
                        or self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}'
                        or not hmac.compare_digest(self.headers.get('Authorization', '').encode('utf-8'),
                                                   ('Bearer ' + bridge._token).encode('utf-8'))):
                    self._reply(403, {'ok': False, 'error': '区域连接未授权。'})
                    return
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    self._reply(400, {'ok': False, 'error': '请求必须是 JSON。'})
                    return
                try:
                    length = int(self.headers.get('Content-Length', '-1'))
                    if not 0 < length <= MAX_RECORD_BYTES or self.headers.get('Transfer-Encoding'):
                        raise ValueError()
                    data = json.loads(self.rfile.read(length))
                    if (not isinstance(data, dict) or set(data) != {'method', 'arguments'}
                            or not isinstance(data['arguments'], dict) or data['method'] not in ('read', 'apply')):
                        raise ValueError()
                except (ValueError, OSError, UnicodeError):
                    self._reply(400, {'ok': False, 'error': '请求格式无效或过大。'})
                    return
                request = {'method': data['method'], 'arguments': data['arguments'],
                           'event': threading.Event(), 'cancelled': threading.Event(),
                           'response': {'ok': False, 'error': '编辑器未连接。'}}
                try:
                    bridge.rpc_requested.emit(request)
                except RuntimeError:
                    self._reply(503, request['response'])
                    return
                if not request['event'].wait(6):
                    request['cancelled'].set()
                    self._reply(503, {'ok': False, 'error': '编辑器忙碌，请重新读取区域确认状态后再试。'})
                    return
                self._reply(200 if request['response']['ok'] else 409, request['response'])

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        self.server = server
        threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.1}, daemon=True).start()

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self.snapshot is not None:
                with self.store.locked():
                    if self._latest():
                        self.store.write(self._record(connected=False))
        except (OSError, RegionError):
            pass
        if self.server is not None:
            server = self.server
            self.server = None
            # Do not block the GUI on active network readers during shutdown.
            threading.Thread(target=lambda: (server.shutdown(), server.server_close()), daemon=True).start()
