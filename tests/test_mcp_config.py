import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from configure_mcp import write_claude_config


class ConfigMergeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'Claude' / 'claude_desktop_config.json'

    def test_new_config_has_no_bom_and_correct_absolute_program(self):
        self.assertIsNone(write_claude_config(self.path, 'D:/CSV Tool/Meng_CSVEditor_MCP.exe', []))
        self.assertFalse(self.path.read_bytes().startswith(b'\xef\xbb\xbf'))
        entry = json.loads(self.path.read_text(encoding='utf-8'))['mcpServers']['meng_csv_editor']
        self.assertEqual(entry['command'], 'D:/CSV Tool/Meng_CSVEditor_MCP.exe')
        self.assertEqual(entry['args'], [])

    def test_existing_config_and_exact_backup_are_preserved(self):
        self.path.parent.mkdir()
        original = b'\xef\xbb\xbf{"theme":"dark","mcpServers":{"existing":{"command":"existing.exe"}}}'
        self.path.write_bytes(original)
        backup = write_claude_config(self.path, 'csv.exe', ['--state-dir', 'D:/my data'])
        self.assertEqual(backup.read_bytes(), original)
        result = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(result['theme'], 'dark')
        self.assertEqual(result['mcpServers']['existing']['command'], 'existing.exe')

    def test_invalid_existing_config_is_not_overwritten(self):
        self.path.parent.mkdir()
        for original in (b'{broken', b'[]', b'{"mcpServers":[]}'):
            self.path.write_bytes(original)
            with self.assertRaises(ValueError):
                write_claude_config(self.path, 'csv.exe', [])
            self.assertEqual(self.path.read_bytes(), original)

    def test_replace_failure_keeps_original_and_cleans_temporary(self):
        self.path.parent.mkdir()
        original = b'{"theme":"dark"}'
        self.path.write_bytes(original)
        with patch('configure_mcp.os.replace', side_effect=OSError('locked')):
            with self.assertRaises(OSError):
                write_claude_config(self.path, 'csv.exe', [])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(list(self.path.parent.glob('*.tmp')))


if __name__ == '__main__':
    unittest.main()
