"""Isolated Qt editor used by real stdio MCP integration tests."""
import faulthandler
import os
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['MENG_CSV_MCP_STATE_DIR'] = sys.argv[1]
faulthandler.enable()
faulthandler.dump_traceback_later(40, exit=True)

from PyQt5.QtCore import QObject, QItemSelection, QItemSelectionModel, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication
from csv_editor import CsvEditorWindow
from ui_theme import apply_theme

app = QApplication([])
apply_theme(app)
settings = MagicMock()
settings.value.side_effect = lambda key, default=None, **kwargs: default

with patch('csv_editor.QSettings', return_value=settings):
    window = CsvEditorWindow()
    window.model.load_data([['字段1', '字段2'], ['名称', '编号'], ['中文\n多行', '001'], ['不提交', 'private']])
    window.undo_stack.clear()
    selection = QItemSelection(window.proxy.index(0, 0), window.proxy.index(1, 1))
    window.table.selectionModel().select(selection, QItemSelectionModel.ClearAndSelect)
    window._show_region_details()
    assert window.region_panel.submit()

    class Control(QObject):
        command = pyqtSignal(str)
        def __init__(self):
            super().__init__()
            self.command.connect(self.handle)
        def handle(self, command):
            if command == 'undo':
                window._undo()
                print('undone', flush=True)
            elif command == 'reset':
                window.model.load_data([['other']])
                window.undo_stack.clear()
                print('reset', flush=True)
            elif command == 'stop':
                window.region_bridge.shutdown()
                window.undo_stack.clear()
                app.quit()
    control = Control()
    def commands():
        for line in sys.stdin:
            control.command.emit(line.strip())
    threading.Thread(target=commands, daemon=True).start()
    QTimer.singleShot(35000, app.quit)
    print('ready', flush=True)
    app.exec_()
    window.region_bridge.shutdown()
