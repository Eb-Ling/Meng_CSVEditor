"""Real official-SDK clients connect over stdio, then read/edit a live Qt app."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def result_data(result):
    return result.structured_content or json.loads(result.content[0].text)


class MCPProtocolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.error_file = (Path(self.directory.name) / 'gui-errors.log').open('w', encoding='utf-8')
        self.addCleanup(self.error_file.close)
        self.gui = subprocess.Popen(
            [sys.executable, str(ROOT / 'tests/mcp_gui_fixture.py'), self.directory.name],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.error_file,
            text=True, encoding='utf-8', env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
        )
        self.addCleanup(self.stop_gui)
        self.assertEqual(self.gui.stdout.readline().strip(), 'ready')

    def stop_gui(self):
        if self.gui.poll() is None:
            try:
                self.gui.stdin.write('stop\n')
                self.gui.stdin.flush()
                self.gui.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                subprocess.run(['taskkill', '/PID', str(self.gui.pid), '/T', '/F'],
                               capture_output=True, timeout=10)
        for pipe in (self.gui.stdin, self.gui.stdout):
            if pipe:
                pipe.close()

    def parameters(self):
        executable = os.environ.get('MENG_MCP_TEST_EXE')
        return StdioServerParameters(command=executable or sys.executable,
                                     args=([] if executable else [str(ROOT / 'mcp_server.py')])
                                          + ['--state-dir', self.directory.name])

    def test_discovery_resource_details_edit_conflict_and_undo_over_stdio(self):
        async def scenario():
            async with stdio_client(self.parameters()) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=12) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual({tool.name for tool in tools.tools},
                                     {'get_latest_region', 'get_cell_details', 'apply_region_edits'})
                    prompts = await session.list_prompts()
                    self.assertIn('explain_region', [prompt.name for prompt in prompts.prompts])
                    latest = await session.call_tool('get_latest_region', {'limit': 2})
                    self.assertFalse(latest.is_error)
                    data = result_data(latest)
                    self.assertEqual(data['total_cells'], 4)
                    self.assertEqual(data['next_offset'], 2)
                    self.assertNotIn('private', json.dumps(data))
                    self.assertNotIn('token', json.dumps(data))
                    resource = await session.read_resource('csv-editor://latest')
                    self.assertEqual(json.loads(resource.contents[0].text)['region_id'], data['region_id'])
                    second = result_data(await session.call_tool('get_latest_region', {'offset': 2, 'limit': 2}))
                    cell = second['cells'][0]
                    detail = result_data(await session.call_tool('get_cell_details',
                        {'region_id': data['region_id'], 'cell_id': cell['cell_id']}))
                    self.assertEqual(detail['value'], '中文\n多行')
                    edit = {'cell_id': cell['cell_id'], 'expected_sha256': cell['sha256'], 'value': 'AI\n解释后修改'}
                    arguments = {'region_id': data['region_id'], 'revision': 0, 'edits': [edit]}
                    result = await session.call_tool('apply_region_edits', arguments)
                    self.assertFalse(result.is_error)
                    self.assertEqual(result_data(result)['changed_cells'], 1)
                    self.assertFalse(result_data(result)['saved_to_csv'])
                    stale = await session.call_tool('apply_region_edits', arguments)
                    self.assertTrue(stale.is_error)
                    self.assertIn('版本已变化', stale.content[0].text)
                    current = result_data(await session.call_tool('get_latest_region', {}))
                    self.assertEqual(current['cells'][2]['value'], 'AI\n解释后修改')
                    self.gui.stdin.write('undo\n'); self.gui.stdin.flush()
                    self.assertEqual(self.gui.stdout.readline().strip(), 'undone')
                    after_undo = result_data(await session.call_tool('get_latest_region', {}))
                    self.assertTrue(after_undo['cells'][2]['changed_since_submission'])
                    conflict = await session.call_tool('apply_region_edits',
                        {'region_id': data['region_id'], 'revision': current['revision'],
                         'edits': [{'cell_id': cell['cell_id'], 'expected_sha256': current['cells'][2]['sha256'], 'value': 'bad'}]})
                    self.assertTrue(conflict.is_error)
        asyncio.run(scenario())

    def test_document_switch_and_invalid_schema_are_tool_errors(self):
        async def scenario():
            async with stdio_client(self.parameters()) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=12) as session:
                    await session.initialize()
                    data = result_data(await session.call_tool('get_latest_region', {}))
                    wrong = await session.call_tool('get_latest_region', {'limit': True})
                    self.assertTrue(wrong.is_error)
                    wrong_page = await session.call_tool('get_latest_region',
                        {'offset': 2, 'expected_region_id': 'previous-region'})
                    self.assertTrue(wrong_page.is_error)
                    self.assertIn('分页期间区域已变化', wrong_page.content[0].text)
                    unknown = await session.call_tool('get_cell_details',
                        {'region_id': data['region_id'], 'cell_id': 'outside'})
                    self.assertTrue(unknown.is_error)
                    self.gui.stdin.write('reset\n'); self.gui.stdin.flush()
                    self.assertEqual(self.gui.stdout.readline().strip(), 'reset')
                    latest = result_data(await session.call_tool('get_latest_region', {}))
                    self.assertFalse(latest['editable'])
                    cell = data['cells'][0]
                    result = await session.call_tool('apply_region_edits',
                        {'region_id': data['region_id'], 'revision': 0,
                         'edits': [{'cell_id': cell['cell_id'], 'expected_sha256': cell['sha256'], 'value': 'wrong doc'}]})
                    self.assertTrue(result.is_error)
        asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main()
