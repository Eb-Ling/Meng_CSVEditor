"""Generate client config or merge the Claude entry without losing settings."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import tempfile
import os

from mcp_config import client_configs, merge_claude_config, server_launch


def write_claude_config(path, command, args):
    path = Path(path)
    if path.exists():
        current_bytes = path.read_bytes()
        current = json.loads(current_bytes.decode('utf-8-sig'))
    else:
        current_bytes, current = None, {}
    merged = merge_claude_config(current, command, args)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if current_bytes is not None:
        backup = path.with_name(path.name + '.' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.bak')
        backup.write_bytes(current_bytes)
    descriptor, temporary = tempfile.mkstemp(prefix='.mcp-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as file:
            descriptor = None
            json.dump(merged, file, ensure_ascii=False, indent=2)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return backup


def main():
    parser = argparse.ArgumentParser(description='Generate Meng CSV Editor MCP client configuration')
    parser.add_argument('--exe-dir', help='使用已打包的 MCP EXE；填写两个 EXE 所在的文件夹。')
    parser.add_argument('--output', default='local-mcp-configs', help='输出配置示例的目录。')
    parser.add_argument('--claude-config', help='合并到指定 Claude Desktop 配置文件；原文件会备份。')
    args = parser.parse_args()
    command, launch_args = server_launch(args.exe_dir, frozen=True) if args.exe_dir else server_launch()
    if not Path(command).is_file():
        raise FileNotFoundError(f'MCP program not found: {command}')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    configs = client_configs(command, launch_args)
    for client, filename in (('Codex', 'codex.toml'), ('Claude Desktop', 'claude.json'),
                             ('通用 MCP 客户端', 'generic.json')):
        (output / filename).write_text(configs[client], encoding='utf-8')
    if args.claude_config:
        backup = write_claude_config(args.claude_config, command, launch_args)
        print(f'Claude configuration updated: {args.claude_config}')
        if backup:
            print(f'Previous configuration backup: {backup}')
    print(f'Configuration examples: {output.resolve()}')


if __name__ == '__main__':
    main()
