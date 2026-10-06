"""Private local region snapshots and the stdio server's GUI bridge client."""
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time


MAX_CELLS = 5000
MAX_TEXT_BYTES = 4 * 1024 * 1024
MAX_RECORD_BYTES = 12 * 1024 * 1024
_PROCESS_LOCK = threading.RLock()


class RegionError(ValueError):
    pass


def value_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def default_state_dir():
    override = os.environ.get('MENG_CSV_MCP_STATE_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    else:
        base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
    return base / 'Meng_CSVEditor' / 'mcp'


class RegionStore:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else default_state_dir()
        self.path = self.directory / 'latest-region.json'

    def read(self):
        try:
            with self.path.open('rb') as file:
                raw = file.read(MAX_RECORD_BYTES + 1)
        except FileNotFoundError:
            return None
        if len(raw) > MAX_RECORD_BYTES:
            raise RegionError('区域缓存过大，请在编辑器中重新提交较小的区域。')
        try:
            record = json.loads(raw)
            snapshot = record['snapshot']
            if (record.get('schema_version') != 1 or not isinstance(snapshot, dict)
                    or not isinstance(snapshot.get('region_id'), str)
                    or type(snapshot.get('revision')) is not int or snapshot['revision'] < 0
                    or not isinstance(snapshot.get('cells'), list) or len(snapshot['cells']) > MAX_CELLS):
                raise ValueError()
            identifiers = set()
            for cell in snapshot['cells']:
                if (not isinstance(cell, dict) or not isinstance(cell.get('cell_id'), str)
                        or cell['cell_id'] in identifiers or not isinstance(cell.get('value'), str)
                        or not isinstance(cell.get('sha256'), str)):
                    raise ValueError()
                identifiers.add(cell['cell_id'])
            return record
        except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
            raise RegionError('区域缓存格式无效，请在编辑器中重新提交区域。') from None

    @contextmanager
    def locked(self):
        # Atomic replace makes reads safe. This small cross-process lock also
        # prevents an older editor window from overwriting a newer submission.
        with _PROCESS_LOCK:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with (self.directory / 'region.lock').open('a+b') as file:
                if file.tell() == 0:
                    file.write(b'0')
                    file.flush()
                file.seek(0)
                acquired = False
                deadline = time.monotonic() + 1
                while not acquired:
                    try:
                        if os.name == 'nt':
                            import msvcrt
                            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise RegionError('另一个窗口正在提交区域，请稍后重试。') from None
                        time.sleep(0.01)
                try:
                    yield
                finally:
                    file.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(file.fileno(), fcntl.LOCK_UN)

    def write(self, record):
        data = json.dumps(record, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(data) > MAX_RECORD_BYTES:
            raise RegionError('区域缓存过大，请减少选中的内容。')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix='.region-', suffix='.tmp', dir=self.directory)
        try:
            with os.fdopen(descriptor, 'wb') as file:
                descriptor = None
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


class RegionClient:
    """Talk only to the loopback endpoint attached to the latest submission."""
    def __init__(self, directory=None):
        self.store = RegionStore(directory)

    def _record(self):
        record = self.store.read()
        if record is None:
            raise RegionError('还没有提交区域。请在 CSV 编辑器中选中区域并点击“将此区域提交给 AI”。')
        return record

    def _request(self, record, method, arguments=None):
        endpoint = record.get('connection')
        if not isinstance(endpoint, dict):
            raise ConnectionError('CSV 编辑器已关闭。')
        port = endpoint.get('port')
        token = endpoint.get('token')
        if (endpoint.get('host') != '127.0.0.1' or type(port) is not int or not 1 <= port <= 65535
                or not isinstance(token, str) or not 32 <= len(token) <= 128):
            raise RegionError('区域连接配置无效，请重新提交区域。')
        data = json.dumps({'method': method, 'arguments': arguments or {}}, ensure_ascii=False).encode('utf-8')
        if len(data) > MAX_RECORD_BYTES:
            raise RegionError('修改请求过大，请分成较小的操作。')
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=8)
        try:
            connection.request('POST', '/rpc', body=data, headers={
                'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
            })
            response = connection.getresponse()
            raw = response.read(MAX_RECORD_BYTES + 1)
            if len(raw) > MAX_RECORD_BYTES:
                raise RegionError('区域响应过大。')
            result = json.loads(raw)
            if response.status != 200 or not result.get('ok'):
                raise RegionError(result.get('error', '区域操作失败。'))
            return result['result']
        finally:
            connection.close()

    def snapshot(self):
        record = self._record()
        try:
            return self._request(record, 'read', {'region_id': record['snapshot']['region_id']})
        except (ConnectionError, OSError, http.client.HTTPException):
            snapshot = dict(record['snapshot'])
            snapshot.update(live_available=False, editable=False,
                            unavailable_reason='编辑器未连接；可阅读最后提交的信息，修改需重新打开并提交。')
            return snapshot

    def apply(self, region_id, revision, edits):
        record = self._record()
        if record['snapshot']['region_id'] != region_id:
            raise RegionError('区域已重新提交，请先读取最新区域。')
        try:
            return self._request(record, 'apply', {'region_id': region_id, 'revision': revision, 'edits': edits})
        except (ConnectionError, OSError, http.client.HTTPException) as error:
            raise RegionError('编辑器连接中断。请重新读取区域确认是否已修改，再决定是否重试。') from error


def region_page(snapshot, offset=0, limit=100):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
        raise RegionError('offset 必须是非负整数，limit 必须是 1 到 500 的整数。')
    result = {key: value for key, value in snapshot.items() if key != 'cells'}
    cells = snapshot['cells']
    result.update(offset=offset, total_cells=len(cells), next_offset=None)
    result['cells'] = []
    for cell in cells[offset:offset + limit]:
        item = dict(cell)
        item['value'] = cell['value'][:2000]
        item['value_truncated'] = len(cell['value']) > 2000
        item['character_count'] = len(cell['value'])
        result['cells'].append(item)
    if offset + limit < len(cells):
        result['next_offset'] = offset + limit
    return result
