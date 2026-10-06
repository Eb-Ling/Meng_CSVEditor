"""Generate portable client configuration; never embed the GUI bridge token."""
import json
from pathlib import Path
import sys


def server_launch(directory=None, frozen=None):
    frozen = getattr(sys, 'frozen', False) if frozen is None else frozen
    if directory is None:
        directory = Path(sys.executable).parent if frozen else Path(__file__).resolve().parent
    directory = Path(directory).resolve()
    if frozen:
        return (directory / 'Meng_CSVEditor_MCP.exe').as_posix(), []
    return Path(sys.executable).resolve().as_posix(), [(directory / 'mcp_server.py').as_posix()]


def client_configs(command=None, args=None):
    if command is None:
        command, args = server_launch()
    args = list(args or [])
    entry = {'command': command, 'args': args}
    codex = ('[mcp_servers.meng_csv_editor]\n'
             f'command = {json.dumps(command, ensure_ascii=False)}\n'
             f'args = {json.dumps(args, ensure_ascii=False)}\n'
             'startup_timeout_sec = 30\n'
             'tool_timeout_sec = 30\n')
    generic = json.dumps({'mcpServers': {'meng_csv_editor': entry}}, ensure_ascii=False, indent=2) + '\n'
    return {'Codex': codex, 'Claude Desktop': generic, '通用 MCP 客户端': generic}


def merge_claude_config(current, command, args):
    if not isinstance(current, dict):
        raise ValueError('Claude 配置根节点必须是 JSON 对象。')
    result = dict(current)
    servers = result.get('mcpServers', {})
    if not isinstance(servers, dict):
        raise ValueError('Claude 配置中的 mcpServers 必须是 JSON 对象。')
    result['mcpServers'] = {**servers, 'meng_csv_editor': {'command': command, 'args': list(args)}}
    return result
