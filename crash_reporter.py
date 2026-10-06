"""Keep unexpected Python errors in Qt callbacks visible and diagnosable."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import traceback

from PyQt5 import sip
from PyQt5.QtCore import QObject, QStandardPaths, Qt
from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout


class ExceptionReporter(QObject):
    """A last-resort callback handler; normal input errors are handled locally."""

    def __init__(self, app, log_path=None, show_dialog=True):
        super().__init__(app)
        self.app = app
        self.log_path = Path(log_path) if log_path else (
            Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
            / 'logs' / 'errors.log'
        )
        self.show_dialog = show_dialog
        self._dialog = None
        self._reporting = False
        self._previous_hook = sys.excepthook

    def install(self):
        sys.excepthook = self.handle_exception
        return self

    def uninstall(self):
        if sys.excepthook == self.handle_exception:
            sys.excepthook = self._previous_hook

    def _write_log(self, exception_type, exception, tb):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            self.log_path, maxBytes=1024 * 1024, backupCount=2, encoding='utf-8',
            errors='backslashreplace'
        )
        try:
            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
            record = logging.LogRecord(
                'Meng_CSVEditor', logging.ERROR, '', 0, 'Unexpected Qt callback error',
                (), (exception_type, exception, tb)
            )
            handler.emit(record)
        finally:
            handler.close()

    @staticmethod
    def _stderr(message):
        try:
            if sys.stderr is not None:
                sys.stderr.write(message + '\n')
        except Exception:
            pass

    def handle_exception(self, exception_type, exception, tb):
        # Exceptions escaping sys.excepthook can cause PyQt to abort the process.
        # In particular, logging permissions and deleted dialogs must not escape.
        if self._reporting:
            self._stderr('Error while reporting a previous application error')
            return
        self._reporting = True
        try:
            details = ''.join(traceback.format_exception(exception_type, exception, tb))
            self._stderr(details)
            logged = False
            try:
                self._write_log(exception_type, exception, tb)
                logged = True
            except Exception as log_error:
                self._stderr('Could not write error log: ' + str(log_error))
            if self.show_dialog:
                if self._dialog is None or sip.isdeleted(self._dialog):
                    self._dialog = QDialog(self.app.activeWindow())
                    self._dialog.setWindowTitle('操作遇到错误')
                    self._dialog.setAttribute(Qt.WA_DeleteOnClose)
                    self._dialog.setWindowModality(Qt.NonModal)
                    layout = QVBoxLayout(self._dialog)
                    message = QLabel('操作未能完成。请检查当前表格内容，再保存或重试。')
                    message.setWordWrap(True)
                    layout.addWidget(message)
                    self._dialog.log_label = QLabel()
                    self._dialog.log_label.setWordWrap(True)
                    self._dialog.log_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                    layout.addWidget(self._dialog.log_label)
                    self._dialog.details = QPlainTextEdit()
                    self._dialog.details.setReadOnly(True)
                    layout.addWidget(self._dialog.details)
                    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
                    buttons.accepted.connect(self._dialog.accept)
                    layout.addWidget(buttons)
                    self._dialog.resize(580, 340)
                self._dialog.log_label.setText(
                    '错误详情已记录到：\n' + str(self.log_path) if logged
                    else '无法写入错误日志，请查看下方详细信息。'
                )
                self._dialog.details.setPlainText(details[-20000:])
                self._dialog.show()
                self._dialog.raise_()
        except Exception as reporting_error:
            self._stderr('Could not display application error: ' + str(reporting_error))
        finally:
            self._reporting = False


def install_exception_handler(app, **kwargs):
    reporter = ExceptionReporter(app, **kwargs).install()
    # Keep the Python bound method alive for the complete application lifetime.
    app._exception_reporter = reporter
    return reporter
