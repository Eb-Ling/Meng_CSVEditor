"""Use isolated processes to verify callback errors never abort Qt's event loop."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CrashReporterTests(unittest.TestCase):
    def run_child(self, body):
        script = '''
import os, sys, tempfile, faulthandler
from pathlib import Path
from unittest.mock import patch
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication
from crash_reporter import ExceptionReporter
app = QApplication([])
faulthandler.dump_traceback_later(10, exit=True)
''' + body
        result = subprocess.run(
            [sys.executable, '-c', script], cwd=ROOT, capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=15,
            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_qt_callback_error_logs_and_next_callback_runs(self):
        result = self.run_child('''
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'errors.log'
    reporter = ExceptionReporter(app, log_path=path, show_dialog=False).install()
    def fail():
        raise RuntimeError('callback regression marker')
    QTimer.singleShot(0, fail)
    QTimer.singleShot(40, app.quit)
    assert app.exec_() == 0
    assert 'callback regression marker' in path.read_text(encoding='utf-8')
    reporter.uninstall()
    print('EVENT_LOOP_SURVIVED')
''')
        self.assertIn('EVENT_LOOP_SURVIVED', result.stdout)

    def test_unwritable_log_does_not_abort_callback(self):
        self.run_child('''
with tempfile.TemporaryDirectory() as directory:
    parent = Path(directory) / 'not_a_directory'
    parent.write_text('occupied')
    reporter = ExceptionReporter(app, log_path=parent / 'errors.log', show_dialog=False).install()
    def fail():
        raise ValueError('logging failure regression marker')
    QTimer.singleShot(0, fail)
    QTimer.singleShot(40, app.quit)
    assert app.exec_() == 0
    reporter.uninstall()
''')

    def test_error_notice_is_nonmodal_and_reuses_open_dialog(self):
        self.run_child('''
with tempfile.TemporaryDirectory() as directory:
    reporter = ExceptionReporter(app, log_path=Path(directory) / 'errors.log').install()
    reporter.handle_exception(ValueError, ValueError('first'), None)
    dialog = reporter._dialog
    assert dialog.isVisible() and not dialog.isModal()
    reporter.handle_exception(ValueError, ValueError('second'), None)
    assert reporter._dialog is dialog
    assert 'second' in dialog.details.toPlainText()
    dialog.close()
    reporter.uninstall()
''')

    def test_reporting_failure_cannot_escape_exception_hook(self):
        self.run_child('''
reporter = ExceptionReporter(app, show_dialog=False).install()
with patch('crash_reporter.traceback.format_exception', side_effect=RuntimeError('reporting failed')):
    reporter.handle_exception(ValueError, ValueError('original'), None)
assert not reporter._reporting
reporter.uninstall()
''')

    def test_surrogate_exception_text_is_logged_without_encoding_failure(self):
        self.run_child('''
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'errors.log'
    reporter = ExceptionReporter(app, log_path=path, show_dialog=False)
    reporter.handle_exception(ValueError, ValueError(chr(0xD800)), None)
    contents = path.read_text(encoding='utf-8')
    assert 'ValueError' in contents and 'd800' in contents
''')

    def test_log_rotation_bounds_accumulated_reports(self):
        self.run_child('''
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'errors.log'
    reporter = ExceptionReporter(app, log_path=path, show_dialog=False)
    for _ in range(5):
        reporter._write_log(ValueError, ValueError('x' * 600000), None)
    logs = list(Path(directory).glob('errors.log*'))
    assert len(logs) == 3
    assert all(log.stat().st_size < 1024 * 1024 for log in logs)
''')


if __name__ == '__main__':
    unittest.main()
