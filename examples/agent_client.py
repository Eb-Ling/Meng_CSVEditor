"""A generic agent can reuse this official-SDK stdio connection pattern."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(command, args):
    async with stdio_client(StdioServerParameters(command=command, args=args)) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=30) as session:
            await session.initialize()
            result = await session.call_tool('get_latest_region', {'offset': 0, 'limit': 100})
            if result.is_error:
                print(result.content[0].text)
                return 1
            data = result.structured_content or json.loads(result.content[0].text)
            region_id, revision = data['region_id'], data['revision']
            metadata = dict(data)
            cells = list(data['cells'])
            while data['next_offset'] is not None:
                result = await session.call_tool('get_latest_region', {
                    'offset': data['next_offset'], 'limit': 100,
                    'expected_region_id': region_id, 'expected_revision': revision,
                })
                if result.is_error:
                    print(result.content[0].text)
                    return 1
                data = result.structured_content or json.loads(result.content[0].text)
                cells.extend(data['cells'])
            for cell in cells:
                if not cell['value_truncated']:
                    continue
                chunks, offset = [], 0
                while offset is not None:
                    result = await session.call_tool('get_cell_details', {
                        'region_id': region_id, 'cell_id': cell['cell_id'],
                        'offset': offset, 'limit': 20000,
                    })
                    if result.is_error:
                        print(result.content[0].text)
                        return 1
                    detail = result.structured_content or json.loads(result.content[0].text)
                    if detail['revision'] != revision or detail['sha256'] != cell['sha256']:
                        print('区域在读取期间变化，请重新运行示例。')
                        return 1
                    chunks.append(detail['value'])
                    offset = detail['next_offset']
                cell['value'] = ''.join(chunks)
                cell['value_truncated'] = False
            metadata.update(cells=cells, offset=0, next_offset=None)
            # Pass this data to your agent's model, or use its normal MCP tool loop.
            print(json.dumps(metadata, ensure_ascii=False, indent=2))
            return 0


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Read the last submitted CSV region over MCP')
    parser.add_argument('--exe', help='完整路径，例如 D:/CSV工具/Meng_CSVEditor_MCP.exe')
    parser.add_argument('--state-dir', help='测试用自定义区域缓存目录。')
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    command, args = (arguments.exe, []) if arguments.exe else (sys.executable, [str(root / 'mcp_server.py')])
    if arguments.state_dir:
        args.extend(['--state-dir', arguments.state_dir])
    return asyncio.run(run(command, args))


if __name__ == '__main__':
    raise SystemExit(main())
