# -*- coding: utf-8 -*-
"""
CSV Editor GUI Layer - 界面层
包含：主窗口、查找替换对话框、行号表头、多行编辑委托、程序入口
仅负责界面展示与用户交互，业务逻辑委托给 csv_model / csv_commands
"""
import sys
import os
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTableView, QHeaderView, QFileDialog,
    QMessageBox, QAction, QToolBar, QStatusBar, QMenu, QDialog,
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QCheckBox, QAbstractItemView, QWidget, QStyledItemDelegate,
    QPlainTextEdit, QStyle
)
from PyQt5.QtCore import (
    Qt, QModelIndex, QSettings, QSortFilterProxyModel, pyqtSignal, QRect, QTimer
)
from PyQt5.QtGui import (
    QKeySequence, QFont, QColor, QPainter, QPalette, QTextOption, QFontMetrics
)
from PyQt5.QtWidgets import QUndoStack

from csv_model import CsvTableModel, read_csv, write_csv
from csv_commands import (
    CellEditCommand, InsertRowsCommand, DeleteRowsCommand,
    InsertColsCommand, DeleteColsCommand, PasteCommand, ClearSelectionCommand
)


# ──────────────────────── Expanding Editor (Viewport Child) ──

class _ExpandingEditor(QPlainTextEdit):
    """
    扩展编辑器：作为 viewport 子控件：
    - WidgetWidth + 词换行 → 文本自动换行显示
    - 禁用滚动条 → 编辑器向外扩展而非内部滚动
    - sizeHint 返回内容真实尺寸
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)

    def sizeHint(self):
        from PyQt5.QtCore import QSize
        fm = self.fontMetrics()
        text = self.toPlainText()
        lines = text.split('\n') if text else ['']
        max_w = max((fm.horizontalAdvance(l) for l in lines), default=0)
        margin = self.contentsMargins()
        w = max_w + margin.left() + margin.right() + 20
        h = fm.lineSpacing() * (self.blockCount() or 1) + margin.top() + margin.bottom() + 10
        return QSize(int(w), int(h))


# ──────────────────────── Multiline Edit Delegate ─────────────

EDITOR_MAX_WIDTH = 700
EDITOR_MAX_HEIGHT = 500
BORDER_COLOR_NORMAL   = QColor(110, 110, 110)
BORDER_COLOR_SELECTED = QColor(60, 140, 240)


class MultilineEditDelegate(QStyledItemDelegate):
    """
    多行编辑委托（viewport 子控件 + 状态机行高管理）：
    - 状态机跟踪：EXPANDED / COLLAPSED
    - 进入编辑 → 记录原始行高 → 扩展行高 → 状态 EXPANDED
    - 退出编辑（任何方式） → 恢复原始行高 → 状态 COLLAPSED
    - closeEditor 信号确保可靠触发恢复
    """

    def __init__(self, undo_stack, table_view=None):
        super().__init__(table_view)
        self.undo_stack = undo_stack
        self._table_view = table_view
        self._old_value = None
        # ── 状态机 ──
        self._state = 'COLLAPSED'          # COLLAPSED | EXPANDED
        self._expanded_row = -1            # 当前扩展的行号
        self._orig_row_height = -1         # 扩展前的原始行高
        # 连接 closeEditor 信号确保可靠恢复
        self.closeEditor.connect(self._on_close_editor)

    def createEditor(self, parent, option, index):
        editor = _ExpandingEditor(parent)
        editor.setTabChangesFocus(False)
        editor.installEventFilter(self)
        editor.setStyleSheet(
            "QPlainTextEdit {"
            "  padding: 5px 7px;"
            f"  border: 2px solid {BORDER_COLOR_SELECTED.name()};"
            "  border-radius: 2px;"
            "}"
        )
        return editor

    def setEditorData(self, editor, index):
        self._old_value = index.data(Qt.EditRole) or ''
        editor.setPlainText(str(self._old_value))
        cursor = editor.textCursor()
        cursor.movePosition(cursor.End)
        editor.setTextCursor(cursor)

    def setModelData(self, editor, model, index):
        new_val = editor.toPlainText()
        old_val = str(self._old_value or '')
        if hasattr(model, 'sourceModel'):
            src_model = model.sourceModel()
            src_idx = model.mapToSource(index)
            r, c = src_idx.row(), src_idx.column()
        else:
            src_model = model
            r, c = index.row(), index.column()

        if old_val != new_val:
            cmd = CellEditCommand(src_model, r, c, old_val, new_val)
            self.undo_stack.push(cmd)

    def updateEditorGeometry(self, editor, option, index):
        """
        COLLAPSED → EXPANDED：记录原始行高 → 扩展行高 → 设置编辑器尺寸
        """
        table_view = self._table_view
        cell_rect = option.rect
        ideal_rect = self._calc_size(editor, cell_rect)

        # 状态转换：COLLAPSED → EXPANDED
        if table_view is not None and self._state == 'COLLAPSED':
            row = index.row()
            self._expanded_row = row
            self._orig_row_height = table_view.rowHeight(row)
            self._state = 'EXPANDED'
            needed_h = ideal_rect.height()
            if needed_h > self._orig_row_height:
                table_view.setRowHeight(row, needed_h)

        editor.setGeometry(ideal_rect)
        QTimer.singleShot(100, lambda: self._reapply_geometry(editor))

    def _calc_size(self, editor, cell_rect):
        """
        根据文本内容计算编辑器理想尺寸：
        - 宽度：用 fontMetrics 按原始行测量（不受编辑器 wrap 模式影响）
        - 高度：按自动折行后的实际行数 × 行高
        """
        fm = editor.fontMetrics()
        text = editor.toPlainText()
        lines = text.split('\n') if text else ['']
        margin = editor.contentsMargins()
        pad_h = margin.left() + margin.right() + 20
        pad_v = margin.top() + margin.bottom() + 10

        # 宽度：按最长原始行的像素宽度
        max_line_w = max((fm.horizontalAdvance(l) for l in lines), default=0)
        ideal_w = max(cell_rect.width(), max_line_w + pad_h)
        ideal_w = min(ideal_w, EDITOR_MAX_WIDTH)

        # 高度：计算文本在编辑器可用宽度内自动折行后的实际行数
        content_w = ideal_w - pad_h
        if content_w > 0:
            wrapped_count = 0
            for line in lines:
                lw = fm.horizontalAdvance(line)
                wrapped_count += max(1, int((lw + content_w - 1) // content_w))
        else:
            wrapped_count = len(lines)
        ideal_h = max(cell_rect.height(), fm.lineSpacing() * wrapped_count + pad_v)
        ideal_h = min(ideal_h, EDITOR_MAX_HEIGHT)

        return QRect(cell_rect.x(), cell_rect.y(), ideal_w, ideal_h)

    def _reapply_geometry(self, editor):
        """延迟强制重设几何 + 行高，覆盖 QTableView 的二次布局"""
        if not editor.isVisible() or self._state != 'EXPANDED':
            return
        table_view = self._table_view
        text = editor.toPlainText()
        lines = text.split('\n') if text else ['']
        fm = editor.fontMetrics()
        margin = editor.contentsMargins()
        pad_h = margin.left() + margin.right() + 20
        pad_v = margin.top() + margin.bottom() + 10
        max_line_w = max((fm.horizontalAdvance(l) for l in lines), default=0)
        ideal_w = min(max(editor.geometry().width(), max_line_w + pad_h), EDITOR_MAX_WIDTH)
        content_w = ideal_w - pad_h
        if content_w > 0:
            wrapped_count = sum(max(1, int((fm.horizontalAdvance(l) + content_w - 1) // content_w)) for l in lines)
        else:
            wrapped_count = len(lines)
        ideal_h = min(max(editor.geometry().height(), fm.lineSpacing() * wrapped_count + pad_v), EDITOR_MAX_HEIGHT)
        # 强制行高
        if table_view is not None and self._expanded_row >= 0:
            if ideal_h > table_view.rowHeight(self._expanded_row):
                table_view.setRowHeight(self._expanded_row, ideal_h)
        if ideal_w != editor.geometry().width() or ideal_h != editor.geometry().height():
            editor.resize(ideal_w, ideal_h)

    def eventFilter(self, editor, event):
        """拦截 Tab 键插入换行，Ctrl+Enter 提交，Esc 取消"""
        from PyQt5.QtCore import QEvent
        if event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Tab and not event.modifiers():
                editor.insertPlainText('\n')
                self._expand_editor(editor)
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) \
               and event.modifiers() & Qt.ControlModifier:
                self.commitData.emit(editor)
                self.closeEditor.emit(editor, QAbstractItemView.NoHint)
                # 行高恢复由 _on_close_editor 信号处理
                return True
            if event.key() == Qt.Key_Escape:
                self.closeEditor.emit(editor, QAbstractItemView.RevertModelCache)
                # 行高恢复由 _on_close_editor 信号处理
                return True
        return super().eventFilter(editor, event)

    def _on_close_editor(self):
        """EXPANDED → COLLAPSED：closeEditor 信号触发时恢复行高"""
        self._collapse_row()

    def _collapse_row(self):
        """状态机：EXPANDED → COLLAPSED，恢复原始行高"""
        if self._state == 'EXPANDED' and self._table_view is not None \
           and self._expanded_row >= 0 and self._orig_row_height > 0:
            self._table_view.setRowHeight(self._expanded_row, self._orig_row_height)
        self._state = 'COLLAPSED'
        self._expanded_row = -1
        self._orig_row_height = -1

    def _expand_editor(self, editor):
        """Tab 换行后重新计算编辑器尺寸"""
        fm = editor.fontMetrics()
        text = editor.toPlainText()
        lines = text.split('\n') if text else ['']
        margin = editor.contentsMargins()
        pad_h = margin.left() + margin.right() + 20
        pad_v = margin.top() + margin.bottom() + 10
        max_line_w = max((fm.horizontalAdvance(l) for l in lines), default=0)
        ideal_w = min(max(editor.width(), max_line_w + pad_h), EDITOR_MAX_WIDTH)
        content_w = ideal_w - pad_h
        if content_w > 0:
            wrapped_count = sum(max(1, int((fm.horizontalAdvance(l) + content_w - 1) // content_w)) for l in lines)
        else:
            wrapped_count = len(lines)
        ideal_h = min(max(editor.height(), fm.lineSpacing() * wrapped_count + pad_v), EDITOR_MAX_HEIGHT)
        # 扩展行高
        if self._state == 'EXPANDED' and self._table_view and self._expanded_row >= 0:
            if ideal_h > self._table_view.rowHeight(self._expanded_row):
                self._table_view.setRowHeight(self._expanded_row, ideal_h)
        editor.resize(ideal_w, ideal_h)
        QTimer.singleShot(100, lambda: self._reapply_geometry(editor))

    def destroyEditor(self, editor, index):
        """编辑器销毁时确保恢复行高（双重保险）"""
        self._collapse_row()
        super().destroyEditor(editor, index)

    def paint(self, painter, option, index):
        """
        绘制单元格：
        - 绘制填充背景与选中高亮
        - 绘制清晰分明的单元格边框（1px 内线 + 外边框）
        - 绘制多行文本内容
        """
        text = index.data(Qt.DisplayRole)
        rect = option.rect
        is_selected = bool(option.state & QStyle.State_Selected)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, False)

        # ── 背景 ──
        if is_selected:
            painter.fillRect(rect, option.palette.highlight())
        elif option.state & QStyle.State_MouseOver:
            hover_color = QColor(option.palette.base().color().lighter(120))
            painter.fillRect(rect, hover_color)
        else:
            painter.fillRect(rect, option.palette.base())

        # ── 文本 ──
        if is_selected:
            painter.setPen(option.palette.highlightedText().color())
        else:
            painter.setPen(option.palette.text().color())

        if text is not None:
            text_rect = rect.adjusted(5, 3, -5, -3)
            painter.drawText(
                text_rect,
                Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                str(text)
            )

        # ── 单元格边框（分明界限）──
        border_pen = BORDER_COLOR_SELECTED if is_selected else BORDER_COLOR_NORMAL
        painter.setPen(border_pen)
        # 绘制矩形边框（右、下边界各缩进 1px，避免与相邻单元格重叠加粗）
        border_rect = QRect(rect.left(), rect.top(),
                            rect.width() - 1, rect.height() - 1)
        painter.drawRect(border_rect)

        painter.restore()


# ──────────────────────── Find/Replace Dialog ─────────────────
class FindReplaceDialog(QDialog):
    find_next = pyqtSignal(str, bool, bool)
    replace_one = pyqtSignal(str, str, bool, bool)
    replace_all = pyqtSignal(str, str, bool, bool)

    def __init__(self, parent=None, show_replace=False):
        super().__init__(parent)
        self.setWindowTitle("替换" if show_replace else "查找")
        self.setMinimumWidth(400)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)

        find_row = QHBoxLayout()
        find_row.addWidget(QLabel("查找:"))
        self.find_edit = QLineEdit()
        self.find_edit.returnPressed.connect(self._on_find)
        find_row.addWidget(self.find_edit)
        self.btn_find = QPushButton("查找下一个")
        self.btn_find.clicked.connect(self._on_find)
        find_row.addWidget(self.btn_find)
        layout.addLayout(find_row)

        if show_replace:
            repl_row = QHBoxLayout()
            repl_row.addWidget(QLabel("替换:"))
            self.replace_edit = QLineEdit()
            repl_row.addWidget(self.replace_edit)
            self.btn_replace = QPushButton("替换")
            self.btn_replace.clicked.connect(self._on_replace)
            repl_row.addWidget(self.btn_replace)
            self.btn_replace_all = QPushButton("全部替换")
            self.btn_replace_all.clicked.connect(self._on_replace_all)
            repl_row.addWidget(self.btn_replace_all)
            layout.addLayout(repl_row)

        opt_row = QHBoxLayout()
        self.chk_case = QCheckBox("区分大小写")
        opt_row.addWidget(self.chk_case)
        self.chk_col = QCheckBox("仅当前列")
        opt_row.addWidget(self.chk_col)
        opt_row.addStretch()
        layout.addLayout(opt_row)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

    def _on_find(self):
        self.find_next.emit(
            self.find_edit.text(),
            self.chk_case.isChecked(),
            self.chk_col.isChecked()
        )

    def _on_replace(self):
        self.replace_one.emit(
            self.find_edit.text(),
            self.replace_edit.text(),
            self.chk_case.isChecked(),
            self.chk_col.isChecked()
        )

    def _on_replace_all(self):
        self.replace_all.emit(
            self.find_edit.text(),
            self.replace_edit.text(),
            self.chk_case.isChecked(),
            self.chk_col.isChecked()
        )


# ──────────────────────── Line Number Header ──────────────────
class RowNumberHeaderView(QHeaderView):
    def __init__(self, parent=None):
        super().__init__(Qt.Vertical, parent)

    def paintSection(self, painter, rect, logicalIndex):
        painter.save()
        painter.fillRect(rect, self.palette().button())
        painter.setPen(self.palette().buttonText().color())
        painter.drawText(rect, Qt.AlignCenter, str(logicalIndex + 1))
        painter.restore()


# ──────────────────────── Main Window ─────────────────────────
class CsvEditorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CSV Editor")
        self.resize(1200, 800)

        self._filepath = None
        self._encoding = 'utf-8'

        # Undo stack（业务层状态管理）
        self.undo_stack = QUndoStack(self)
        self.undo_stack.cleanChanged.connect(self._on_clean_changed)

        # 业务层 Model
        self.model = CsvTableModel(self)

        # 排序代理
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)

        # 表格视图
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionsMovable(True)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        # 网格线：与委托绘制的边框颜色保持一致，使整体界限分明
        self.table.setShowGrid(True)
        self.table.setGridStyle(Qt.SolidLine)
        self.table.setStyleSheet(
            self.table.styleSheet() +
            "\nQTableView { gridline-color: rgb(110, 110, 110); }"
        )
        # 默认行高略微增大，使单元格视觉更舒适
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        # 行号表头
        self.row_header = RowNumberHeaderView(self.table)
        self.table.setVerticalHeader(self.row_header)

        # 多行编辑委托（Tab 换行功能在此）
        self.delegate = MultilineEditDelegate(self.undo_stack, self.table)
        self.table.setItemDelegate(self.delegate)

        self.setCentralWidget(self.table)

        # 状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.lbl_position = QLabel("就绪")
        self.lbl_size = QLabel("")
        self.status_bar.addWidget(self.lbl_position, 1)
        self.status_bar.addPermanentWidget(self.lbl_size)

        self.table.selectionModel().currentChanged.connect(self._update_status)

        self._build_menus()
        self._build_toolbar()

        self.find_dialog = None
        self.replace_dialog = None

        self._new_file()

    # ────────── Menu ──────────
    def _build_menus(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件(&F)")
        self.act_new = QAction("新建(&N)", self, shortcut=QKeySequence.New, triggered=self._new_file)
        self.act_open = QAction("打开(&O)...", self, shortcut=QKeySequence.Open, triggered=self._open_file)
        self.act_save = QAction("保存(&S)", self, shortcut=QKeySequence.Save, triggered=self._save_file)
        self.act_save_as = QAction("另存为(&A)...", self, shortcut=QKeySequence("Ctrl+Shift+S"), triggered=self._save_as)
        self.act_quit = QAction("退出(&Q)", self, shortcut=QKeySequence.Quit, triggered=self.close)

        self.recent_menu = QMenu("最近文件(&R)", self)
        self._load_recent()

        file_menu.addAction(self.act_new)
        file_menu.addAction(self.act_open)
        file_menu.addSeparator()
        file_menu.addAction(self.act_save)
        file_menu.addAction(self.act_save_as)
        file_menu.addSeparator()
        file_menu.addMenu(self.recent_menu)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        edit_menu = menubar.addMenu("编辑(&E)")
        self.act_undo = self.undo_stack.createUndoAction(self, "撤销(&U)")
        self.act_undo.setShortcut(QKeySequence.Undo)
        self.act_redo = self.undo_stack.createRedoAction(self, "重做(&R)")
        self.act_redo.setShortcut(QKeySequence.Redo)
        self.act_copy = QAction("复制(&C)", self, shortcut=QKeySequence.Copy, triggered=self._copy)
        self.act_cut = QAction("剪切(&X)", self, shortcut=QKeySequence.Cut, triggered=self._cut)
        self.act_paste = QAction("粘贴(&V)", self, shortcut=QKeySequence.Paste, triggered=self._paste)
        self.act_delete = QAction("删除(&D)", self, shortcut=QKeySequence.Delete, triggered=self._delete_selection)

        edit_menu.addAction(self.act_undo)
        edit_menu.addAction(self.act_redo)
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_copy)
        edit_menu.addAction(self.act_cut)
        edit_menu.addAction(self.act_paste)
        edit_menu.addAction(self.act_delete)
        edit_menu.addSeparator()

        self.act_ins_row = QAction("插入行(上方)", self, shortcut=QKeySequence("Ctrl+I"), triggered=self._insert_row_above)
        self.act_ins_row_below = QAction("插入行(下方)", self, shortcut=QKeySequence("Ctrl+Shift+I"), triggered=self._insert_row_below)
        self.act_del_row = QAction("删除行", self, shortcut=QKeySequence("Ctrl+D"), triggered=self._delete_row)
        self.act_ins_col = QAction("插入列(左侧)", self, shortcut=QKeySequence("Ctrl+J"), triggered=self._insert_col_left)
        self.act_ins_col_right = QAction("插入列(右侧)", self, shortcut=QKeySequence("Ctrl+Shift+J"), triggered=self._insert_col_right)
        self.act_del_col = QAction("删除列", self, shortcut=QKeySequence("Ctrl+Shift+D"), triggered=self._delete_col)

        edit_menu.addAction(self.act_ins_row)
        edit_menu.addAction(self.act_ins_row_below)
        edit_menu.addAction(self.act_del_row)
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_ins_col)
        edit_menu.addAction(self.act_ins_col_right)
        edit_menu.addAction(self.act_del_col)

        search_menu = menubar.addMenu("查找(&S)")
        self.act_find = QAction("查找(&F)...", self, shortcut=QKeySequence.Find, triggered=self._show_find)
        self.act_replace = QAction("替换(&H)...", self, shortcut=QKeySequence.Replace, triggered=self._show_replace)
        search_menu.addAction(self.act_find)
        search_menu.addAction(self.act_replace)

        help_menu = menubar.addMenu("帮助(&H)")
        help_menu.addAction("关于", self._show_about)

    def _build_toolbar(self):
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addAction(self.act_new)
        tb.addAction(self.act_open)
        tb.addAction(self.act_save)
        tb.addSeparator()
        tb.addAction(self.act_undo)
        tb.addAction(self.act_redo)
        tb.addSeparator()
        tb.addAction(self.act_find)
        tb.addAction(self.act_replace)

    # ────────── File operations ──────────
    def _new_file(self):
        if not self._confirm_discard():
            return
        self.model.load_data([['', '', '', '', ''], ['', '', '', '', '']])
        self._filepath = None
        self._encoding = 'utf-8'
        self.undo_stack.clear()
        self._update_title()
        self._update_size_label()

    def _open_file(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 CSV 文件", "",
            "CSV 文件 (*.csv *.tsv *.txt);;所有文件 (*)"
        )
        if not path:
            return
        self._load_file(path)

    def _load_file(self, path):
        try:
            rows, enc = read_csv(path)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法打开文件:\n{e}")
            return
        if not rows:
            rows = [['']]
        self.model.load_data(rows)
        self._filepath = path
        self._encoding = enc
        self.undo_stack.clear()
        self.undo_stack.setClean()
        self._update_title()
        self._update_size_label()
        self._add_recent(path)

    def _save_file(self):
        if not self._filepath:
            self._save_as()
            return
        try:
            write_csv(self._filepath, self.model.get_all_data(), self._encoding)
            self.undo_stack.setClean()
            self._update_title()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存失败:\n{e}")

    def _save_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为", "",
            "CSV 文件 (*.csv);;TSV 文件 (*.tsv);;所有文件 (*)"
        )
        if not path:
            return
        self._filepath = path
        self._encoding = 'utf-8'
        self._save_file()
        self._add_recent(path)

    def _confirm_discard(self):
        if self.undo_stack.isClean():
            return True
        ret = QMessageBox.question(
            self, "未保存的更改",
            "当前文件有未保存的更改，是否保存？",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save
        )
        if ret == QMessageBox.Save:
            self._save_file()
            return True
        elif ret == QMessageBox.Discard:
            return True
        return False

    def _update_title(self):
        name = os.path.basename(self._filepath) if self._filepath else "未命名"
        dirty = "" if self.undo_stack.isClean() else " *"
        self.setWindowTitle(f"{name}{dirty} - CSV Editor")

    def _update_size_label(self):
        r = self.model.rowCount()
        c = self.model.columnCount()
        self.lbl_size.setText(f"{r} 行 x {c} 列")

    def _on_clean_changed(self, clean):
        self._update_title()

    def _update_status(self, current, previous):
        if current.isValid():
            src = self.proxy.mapToSource(current)
            self.lbl_position.setText(
                f"行: {src.row()+1}  列: {CsvTableModel._col_letter(src.column())} ({src.column()+1})"
            )

    # ────────── Recent files ──────────
    def _load_recent(self):
        self.recent_menu.clear()
        settings = QSettings("CsvEditor", "CsvEditor")
        files = settings.value("recent_files", []) or []
        for f in files:
            if os.path.exists(f):
                act = self.recent_menu.addAction(f)
                act.setData(f)
                act.triggered.connect(lambda checked, path=f: self._open_recent(path))
        if not files:
            self.recent_menu.addAction("(空)").setEnabled(False)

    def _add_recent(self, path):
        settings = QSettings("CsvEditor", "CsvEditor")
        files = settings.value("recent_files", []) or []
        if path in files:
            files.remove(path)
        files.insert(0, path)
        files = files[:10]
        settings.setValue("recent_files", files)
        self._load_recent()

    def _open_recent(self, path):
        if self._confirm_discard():
            self._load_file(path)

    # ────────── Context menu ──────────
    def _show_context_menu(self, pos):
        menu = QMenu(self)
        menu.addAction("插入行(上方)", self._insert_row_above)
        menu.addAction("插入行(下方)", self._insert_row_below)
        menu.addAction("删除行", self._delete_row)
        menu.addSeparator()
        menu.addAction("插入列(左侧)", self._insert_col_left)
        menu.addAction("插入列(右侧)", self._insert_col_right)
        menu.addAction("删除列", self._delete_col)
        menu.addSeparator()
        menu.addAction("复制", self._copy)
        menu.addAction("剪切", self._cut)
        menu.addAction("粘贴", self._paste)
        menu.addAction("删除", self._delete_selection)
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    # ────────── Row/Col operations (undo-aware) ──────────
    def _current_source_index(self):
        idx = self.table.currentIndex()
        if idx.isValid():
            return self.proxy.mapToSource(idx)
        return QModelIndex()

    def _insert_row_above(self):
        src = self._current_source_index()
        row = src.row() if src.isValid() else self.model.rowCount()
        self.undo_stack.push(InsertRowsCommand(self.model, row))
        self._update_size_label()

    def _insert_row_below(self):
        src = self._current_source_index()
        row = (src.row() + 1) if src.isValid() else self.model.rowCount()
        self.undo_stack.push(InsertRowsCommand(self.model, row))
        self._update_size_label()

    def _delete_row(self):
        src = self._current_source_index()
        if not src.isValid():
            return
        row = src.row()
        saved = [list(self.model._data[row])]
        self.undo_stack.push(DeleteRowsCommand(self.model, row, 1, saved))
        self._update_size_label()

    def _insert_col_left(self):
        src = self._current_source_index()
        col = src.column() if src.isValid() else self.model.columnCount()
        self.undo_stack.push(InsertColsCommand(self.model, col))
        self._update_size_label()

    def _insert_col_right(self):
        src = self._current_source_index()
        col = (src.column() + 1) if src.isValid() else self.model.columnCount()
        self.undo_stack.push(InsertColsCommand(self.model, col))
        self._update_size_label()

    def _delete_col(self):
        src = self._current_source_index()
        if not src.isValid():
            return
        col = src.column()
        saved = []
        for r in range(self.model.rowCount()):
            if col < len(self.model._data[r]):
                saved.append([self.model._data[r][col]])
            else:
                saved.append([''])
        self.undo_stack.push(DeleteColsCommand(self.model, col, 1, saved))
        self._update_size_label()

    # ────────── Clipboard ──────────
    def _get_selection_bounds(self):
        sel = self.table.selectionModel().selectedIndexes()
        if not sel:
            return None
        src_indices = [self.proxy.mapToSource(idx) for idx in sel]
        rows = [idx.row() for idx in src_indices]
        cols = [idx.column() for idx in src_indices]
        return min(rows), min(cols), max(rows), max(cols)

    def _copy(self):
        bounds = self._get_selection_bounds()
        if not bounds:
            return
        r1, c1, r2, c2 = bounds
        lines = []
        for r in range(r1, r2 + 1):
            row_vals = []
            for c in range(c1, c2 + 1):
                if r < len(self.model._data) and c < len(self.model._data[r]):
                    row_vals.append(self.model._data[r][c])
                else:
                    row_vals.append('')
            lines.append('\t'.join(row_vals))
        QApplication.clipboard().setText('\n'.join(lines))

    def _cut(self):
        self._copy()
        self._delete_selection()

    def _paste(self):
        text = QApplication.clipboard().text()
        if not text:
            return
        src = self._current_source_index()
        if not src.isValid():
            return
        sr, sc = src.row(), src.column()

        lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        if lines and lines[-1] == '':
            lines.pop()
        new_block = [line.split('\t') for line in lines]
        rows = len(new_block)
        cols = max(len(r) for r in new_block)
        for r in new_block:
            while len(r) < cols:
                r.append('')

        while self.model.rowCount() < sr + rows:
            self.model.insertRows(self.model.rowCount(), 1)
        while self.model.columnCount() < sc + cols:
            self.model.insertColumns(self.model.columnCount(), 1)

        old_block = self.model.get_block(sr, sc, rows, cols)
        self.undo_stack.push(PasteCommand(self.model, sr, sc, old_block, new_block))

    def _delete_selection(self):
        sel = self.table.selectionModel().selectedIndexes()
        if not sel:
            return
        cells = []
        for idx in sel:
            src = self.proxy.mapToSource(idx)
            r, c = src.row(), src.column()
            if r < len(self.model._data) and c < len(self.model._data[r]):
                cells.append((r, c, self.model._data[r][c]))
        if cells:
            self.undo_stack.push(ClearSelectionCommand(self.model, cells))

    # ────────── Find / Replace ──────────
    def _show_find(self):
        if self.find_dialog is None or not self.find_dialog.isVisible():
            self.find_dialog = FindReplaceDialog(self, show_replace=False)
            self.find_dialog.find_next.connect(self._do_find)
        self.find_dialog.show()
        self.find_dialog.raise_()
        self.find_dialog.activateWindow()

    def _show_replace(self):
        if self.replace_dialog is None or not self.replace_dialog.isVisible():
            self.replace_dialog = FindReplaceDialog(self, show_replace=True)
            self.replace_dialog.find_next.connect(self._do_find)
            self.replace_dialog.replace_one.connect(self._do_replace)
            self.replace_dialog.replace_all.connect(self._do_replace_all)
        self.replace_dialog.show()
        self.replace_dialog.raise_()
        self.replace_dialog.activateWindow()

    def _do_find(self, text, case_sensitive, col_only):
        if not text:
            return
        data = self.model._data
        if not data:
            return

        src = self._current_source_index()
        start_r = src.row() if src.isValid() else 0
        start_c = src.column() if src.isValid() else 0

        nrows = len(data)
        ncols = len(data[0]) if data else 0

        found = False
        r, c = start_r, start_c
        c += 1
        if c >= ncols:
            c = 0
            r += 1

        for _ in range(nrows * ncols):
            if r >= nrows:
                r = 0
                c = 0
            if col_only and c != start_c:
                c = start_c
            if c >= ncols:
                c = 0
                r += 1
                if r >= nrows:
                    r = 0

            val = data[r][c] if c < len(data[r]) else ''
            if case_sensitive:
                match = text in val
            else:
                match = text.lower() in val.lower()

            if match:
                found = True
                break

            c += 1
            if c >= ncols:
                c = 0
                r += 1

        if found:
            idx = self.model.index(r, c)
            proxy_idx = self.proxy.mapFromSource(idx)
            self.table.setCurrentIndex(proxy_idx)
            self.table.scrollTo(proxy_idx)
            self._set_find_status("", "")
        else:
            self._set_find_status("未找到匹配项", "orange")

    def _do_replace(self, find_text, repl_text, case_sensitive, col_only):
        src = self._current_source_index()
        if not src.isValid():
            return
        r, c = src.row(), src.column()
        if r < len(self.model._data) and c < len(self.model._data[r]):
            val = self.model._data[r][c]
            if case_sensitive:
                match = find_text in val
            else:
                match = find_text.lower() in val.lower()
            if match:
                old_val = val
                new_val = (
                    val.replace(find_text, repl_text)
                    if case_sensitive
                    else self._case_insensitive_replace(val, find_text, repl_text)
                )
                self.undo_stack.push(CellEditCommand(self.model, r, c, old_val, new_val))
        self._do_find(find_text, case_sensitive, col_only)

    def _do_replace_all(self, find_text, repl_text, case_sensitive, col_only):
        if not find_text:
            return
        data = self.model._data
        count = 0
        for r in range(len(data)):
            for c in range(len(data[r])):
                val = data[r][c]
                if case_sensitive:
                    match = find_text in val
                else:
                    match = find_text.lower() in val.lower()
                if match:
                    new_val = (
                        val.replace(find_text, repl_text)
                        if case_sensitive
                        else self._case_insensitive_replace(val, find_text, repl_text)
                    )
                    self.model._set_data(r, c, new_val)
                    count += 1
        if count > 0:
            self.undo_stack.setClean()
            self._set_find_status(f"已替换 {count} 处", "green")
        else:
            self._set_find_status("未找到匹配项", "orange")

    @staticmethod
    def _case_insensitive_replace(text, find, repl):
        result = []
        lower_text = text.lower()
        lower_find = find.lower()
        i = 0
        while i < len(text):
            if lower_text[i:i+len(lower_find)] == lower_find:
                result.append(repl)
                i += len(lower_find)
            else:
                result.append(text[i])
                i += 1
        return ''.join(result)

    def _set_find_status(self, msg, color):
        dialog = self.find_dialog or self.replace_dialog
        if dialog:
            dialog.status_label.setText(msg)
            dialog.status_label.setStyleSheet(f"color: {color};" if color else "")

    # ────────── About ──────────
    def _show_about(self):
        QMessageBox.about(
            self, "关于 CSV Editor",
            "CSV Editor Tool\n\n"
            "基于 PyQt5 的轻量级 CSV 编辑器\n"
            "支持网格编辑、多行单元格、行列操作、查找替换、撤销重做\n\n"
            "提示：在单元格编辑时按 Tab 可插入换行，Ctrl+Enter 提交编辑"
        )

    # ────────── Close event ──────────
    def closeEvent(self, event):
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()


# ──────────────────────── Entry Point ─────────────────────────
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("CSV Editor")
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(53, 53, 53))
    palette.setColor(QPalette.WindowText, Qt.white)
    palette.setColor(QPalette.Base, QColor(42, 42, 42))
    palette.setColor(QPalette.AlternateBase, QColor(66, 66, 66))
    palette.setColor(QPalette.ToolTipBase, Qt.white)
    palette.setColor(QPalette.ToolTipText, Qt.white)
    palette.setColor(QPalette.Text, Qt.white)
    palette.setColor(QPalette.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ButtonText, Qt.white)
    palette.setColor(QPalette.BrightText, Qt.red)
    palette.setColor(QPalette.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.HighlightedText, Qt.black)
    app.setPalette(palette)

    window = CsvEditorWindow()

    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        window._load_file(sys.argv[1])

    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
