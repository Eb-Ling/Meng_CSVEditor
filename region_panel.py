"""Collapsible selection inspector; data is published only by explicit Submit."""
from PyQt5.QtCore import QModelIndex, QPersistentModelIndex, Qt, QTimer, QEvent
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDockWidget,
    QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QToolButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from csv_model import CsvTableModel
from mcp_config import client_configs
from region_store import MAX_CELLS, RegionError


class MCPConnectionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('连接 AI 客户端')
        self.resize(640, 440)
        layout = QVBoxLayout(self)
        intro = QLabel('把下面的配置添加到对应客户端，重新连接 MCP 后，即可在对话中读取和修改提交的区域。')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.clients = QComboBox()
        self.configs = client_configs()
        self.clients.addItems(self.configs)
        layout.addWidget(self.clients)
        self.configuration = QPlainTextEdit()
        self.configuration.setReadOnly(True)
        layout.addWidget(self.configuration)
        self.destination = QLabel()
        self.destination.setWordWrap(True)
        layout.addWidget(self.destination)
        copy = QPushButton('复制配置')
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.configuration.toPlainText()))
        layout.addWidget(copy)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.clients.currentTextChanged.connect(self._update)
        self._update(self.clients.currentText())

    def _update(self, client):
        self.configuration.setPlainText(self.configs[client])
        destinations = {
            'Codex': '配置文件：用户目录下的 .codex/config.toml；也可在客户端的 MCP 设置中填写 command 和 args。',
            'Claude Desktop': 'Windows 配置文件：%APPDATA%/Claude/claude_desktop_config.json。保留已有配置，把此服务器合并到 mcpServers。',
            '通用 MCP 客户端': '连接类型选择 STDIO。command 填可执行程序的完整路径，args 填参数列表；具体配置结构以客户端说明为准。',
        }
        self.destination.setText(destinations[client])


class RegionPanel(QDockWidget):
    def __init__(self, window, bridge):
        super().__init__('区域详情 · AI', window)
        self.window, self.bridge = window, bridge
        self.setObjectName('regionDock')
        self.setAllowedAreas(Qt.RightDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetClosable)
        self.selected = []
        self.collapsed = False
        self._expanded_width = 360
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh_cells)
        title = QWidget()
        title_layout = QHBoxLayout(title)
        title_layout.setContentsMargins(6, 5, 6, 5)
        self.collapse_button = QToolButton()
        self.collapse_button.setArrowType(Qt.RightArrow)
        self.collapse_button.setToolTip('折叠区域面板')
        self.collapse_button.clicked.connect(self.toggle_collapsed)
        title_layout.addWidget(self.collapse_button)
        self.heading = QLabel('区域详情 · AI')
        title_layout.addWidget(self.heading, 1)
        self.close_button = QToolButton()
        self.close_button.setText('×')
        self.close_button.setToolTip('关闭区域面板')
        self.close_button.clicked.connect(self.hide)
        title_layout.addWidget(self.close_button)
        self.setTitleBarWidget(title)
        self.body = QWidget()
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(12, 10, 12, 12)
        self.summary = QLabel('选中区域后，在右键菜单中选择“查看区域详情”。')
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.cells = QTreeWidget()
        self.cells.setHeaderLabels(['单元格', '内容预览'])
        self.cells.setRootIsDecorated(False)
        self.cells.setAlternatingRowColors(True)
        self.cells.setColumnWidth(0, 72)
        self.cells.currentItemChanged.connect(self._show_cell)
        layout.addWidget(self.cells, 2)
        self.cell_meta = QLabel('选择一个单元格查看完整内容')
        self.cell_meta.setWordWrap(True)
        layout.addWidget(self.cell_meta)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText('这里会显示单元格的完整文字、空格和换行。')
        layout.addWidget(self.detail, 3)
        self.allow_edits = QCheckBox('允许 AI 修改此区域')
        self.allow_edits.setChecked(True)
        self.allow_edits.toggled.connect(self._permission_changed)
        layout.addWidget(self.allow_edits)
        self.submit_button = QPushButton('将此区域提交给 AI')
        self.submit_button.setDefault(True)
        self.submit_button.setEnabled(False)
        self.submit_button.clicked.connect(self.submit)
        layout.addWidget(self.submit_button)
        self.status = QLabel('尚未提交。提交后，在已连接的 AI 客户端中对话。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        connect = QPushButton('连接说明')
        connect.clicked.connect(self.show_connection)
        copy_prompt = QPushButton('复制对话提示')
        copy_prompt.clicked.connect(self.copy_prompt)
        actions.addWidget(connect)
        actions.addWidget(copy_prompt)
        layout.addLayout(actions)
        self.setWidget(self.body)
        for child in self.body.findChildren(QWidget):
            child.installEventFilter(self)
        self.setMinimumWidth(280)
        self.setMaximumWidth(650)
        bridge.changed.connect(self._bridge_changed)
        bridge.accessed.connect(self.status.setText)
        window.model.dataChanged.connect(self._data_changed)
        window.model.modelReset.connect(self._model_reset)
        window.model.rowsRemoved.connect(self._data_changed)
        window.model.columnsRemoved.connect(self._data_changed)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.ShortcutOverride, QEvent.KeyPress):
            control = bool(event.modifiers() & Qt.ControlModifier)
            key = event.key()
            blocked = key in (Qt.Key_Delete, Qt.Key_Backspace) or (
                control and key in (Qt.Key_X, Qt.Key_V, Qt.Key_D, Qt.Key_I, Qt.Key_J))
            copying = control and key == Qt.Key_C
            if blocked or copying:
                event.accept()
                if event.type() == QEvent.KeyPress and copying:
                    if watched is self.detail or self.detail.isAncestorOf(watched):
                        self.detail.copy()
                    else:
                        item = self.cells.currentItem()
                        if item is not None:
                            position = item.data(0, Qt.UserRole)
                            if type(position) is int and 0 <= position < len(self.selected):
                                index = self.selected[position][0]
                                if index.isValid():
                                    QApplication.clipboard().setText(str(index.data(Qt.EditRole) or ''))
                return True
        return super().eventFilter(watched, event)

    def show_region(self, selected):
        if not selected:
            self.status.setText('请先选中需要查看的区域。')
            return
        if len(selected) > MAX_CELLS:
            self.window.status_bar.showMessage(f'一次最多查看 {MAX_CELLS} 个单元格，请缩小区域。', 8000)
            return
        self.selected = [(QPersistentModelIndex(index), row, column) for index, row, column in selected]
        self.cells.clear()
        for position, (index, _row, _column) in enumerate(self.selected):
            value = str(index.data(Qt.EditRole) or '')
            address = CsvTableModel._col_letter(index.column()) + str(index.row() + 1)
            item = QTreeWidgetItem([address, value.replace('\r', ' ').replace('\n', ' ↵ ')[:100]])
            item.setData(0, Qt.UserRole, position)
            self.cells.addTopLevelItem(item)
        rows = [row for _, row, _ in selected]
        cols = [column for _, _, column in selected]
        self.summary.setText(f'显示区域 {CsvTableModel._col_letter(min(cols))}{min(rows)+1}:'
                             f'{CsvTableModel._col_letter(max(cols))}{max(rows)+1} · {len(selected)} 个单元格')
        self.submit_button.setEnabled(True)
        self.submit_button.setText('将此区域提交给 AI')
        self.status.setText('尚未提交当前区域。点击列表中的单元格查看完整内容。')
        self.cells.setCurrentItem(self.cells.topLevelItem(0))
        if self.collapsed:
            self.toggle_collapsed()
        self.show()
        self.window.resizeDocks([self], [self._expanded_width], Qt.Horizontal)

    def toggle_collapsed(self):
        if not self.collapsed:
            self._expanded_width = max(280, min(650, self.width()))
        self.collapsed = not self.collapsed
        self.body.setVisible(not self.collapsed)
        self.heading.setVisible(not self.collapsed)
        self.close_button.setVisible(not self.collapsed)
        self.collapse_button.setArrowType(Qt.LeftArrow if self.collapsed else Qt.RightArrow)
        self.collapse_button.setToolTip('展开区域面板' if self.collapsed else '折叠区域面板')
        self.setMinimumWidth(40 if self.collapsed else 280)
        self.setMaximumWidth(40 if self.collapsed else 650)
        self.window.resizeDocks([self], [40 if self.collapsed else self._expanded_width], Qt.Horizontal)

    def _show_cell(self, item, _previous=None):
        if item is None:
            self.detail.clear()
            return
        position = item.data(0, Qt.UserRole)
        if type(position) is not int or not 0 <= position < len(self.selected):
            return
        index = self.selected[position][0]
        if not index.isValid():
            self.detail.clear()
            self.cell_meta.setText('该单元格已删除，请重新选择区域。')
            return
        value = str(index.data(Qt.EditRole) or '')
        address = CsvTableModel._col_letter(index.column()) + str(index.row() + 1)
        self.cell_meta.setText(f'{address} · {len(value)} 字符 · {len(value.splitlines()) or 1} 行')
        self.detail.setPlainText(value)

    def _data_changed(self, *_args):
        if not self._refresh_timer.isActive():
            self._refresh_timer.start(0)

    def _refresh_cells(self):
        for position, (index, _, _) in enumerate(self.selected):
            item = self.cells.topLevelItem(position)
            if item is None:
                continue
            if index.isValid():
                value = str(index.data(Qt.EditRole) or '')
                item.setText(0, CsvTableModel._col_letter(index.column()) + str(index.row() + 1))
                item.setText(1, value.replace('\r', ' ').replace('\n', ' ↵ ')[:100])
            else:
                item.setText(1, '已删除')
        self._show_cell(self.cells.currentItem())
        valid = bool(self.selected) and all(index.isValid() for index, _, _ in self.selected)
        self.submit_button.setEnabled(valid)
        self._bridge_changed()

    def _model_reset(self):
        self.selected = []
        self.cells.clear()
        self.detail.clear()
        self.submit_button.setEnabled(False)
        self.cell_meta.setText('文档已切换，请重新选择区域。')

    def _bridge_changed(self):
        if self.bridge.snapshot is not None and not self.bridge._binding_valid():
            self.status.setText('已提交区域已失效，AI 可以阅读原信息；修改前请重新选择并提交。')

    def _permission_changed(self, allowed):
        try:
            self.bridge.set_edit_allowed(allowed)
        except (OSError, RegionError) as error:
            self.status.setText(f'权限设置未能保存：{error}')

    def submit(self):
        self.window.delegate.commit_active_editor()
        try:
            result = self.bridge.submit([(QModelIndex(index), row, col) for index, row, col in self.selected],
                                        self.allow_edits.isChecked())
        except (OSError, RegionError, UnicodeError, MemoryError) as error:
            self.status.setText(f'提交失败：{error}')
            return False
        self.status.setText(f'已提交 {len(result["cells"])} 个单元格。现在可以到 AI 客户端提问；AI 修改可按 Ctrl+Z 撤销。')
        self.submit_button.setText('重新提交此区域')
        return True

    def show_connection(self):
        dialog = MCPConnectionDialog(self.window)
        dialog.exec_()
        dialog.deleteLater()

    def copy_prompt(self):
        QApplication.clipboard().setText(
            '请使用 meng_csv_editor MCP 的 get_latest_region 读取我最后提交的 CSV 区域。'
            '先解释每个字段和数据含义；如果我要求修改，请只修改该区域，保留前导零、空格和换行，修改前核对最新版本。'
        )
        self.status.setText('对话提示已复制，粘贴到已连接 MCP 的 AI 客户端即可。')
