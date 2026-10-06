# -*- coding: utf-8 -*-
"""
Meng_CSVEditor GUI Layer - 界面层
包含：主窗口、查找替换对话框、行号表头、多行编辑委托、程序入口
仅负责界面展示与用户交互，业务逻辑委托给 csv_model / csv_commands
"""
import sys
import os
import csv
import io
import re
from dataclasses import replace
from PyQt5 import sip
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTableView, QHeaderView, QFileDialog,
    QMessageBox, QAction, QToolBar, QStatusBar, QMenu, QDialog,
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QCheckBox, QAbstractItemView, QWidget, QStyledItemDelegate,
    QPlainTextEdit, QStyle, QDialogButtonBox, QFrame, QToolButton,
    QSizePolicy, QStyleOptionViewItem
)
from PyQt5.QtCore import (
    Qt, QModelIndex, QPersistentModelIndex, QSettings, QSortFilterProxyModel,
    pyqtSignal, QRect, QTimer, QEvent, QSize
)
from PyQt5.QtGui import (
    QKeySequence, QFont, QColor, QPainter, QPalette, QTextOption, QFontMetrics, QPen
)

from csv_model import (CsvTableModel, CsvFormat, csv_format_for_save,
                       parse_csv_text, read_csv_document, write_csv)
from csv_commands import (
    SafeUndoStack, CellEditCommand, InsertRowsCommand, DeleteRowsCommand,
    InsertColsCommand, DeleteColsCommand, PasteCellsCommand, BatchEditCommand,
    ClearSelectionCommand
)
from ui_theme import (
    apply_theme, make_icon, SURFACE, TEXT, MUTED, GRID, ACCENT,
    SELECTION, ALTERNATE_ROW,
)


class ElidedLabel(QLabel):
    """Keep filenames and paths readable without stretching the window."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_text = ''
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def set_full_text(self, text):
        self._full_text = str(text)
        self.setToolTip(self._full_text)
        self._refresh()

    def _refresh(self):
        self.setText(self.fontMetrics().elidedText(
            self._full_text, Qt.ElideMiddle, max(0, self.contentsRect().width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()


# ──────────────────────── Expanding Editor (Viewport Child) ──

class _ExpandingEditor(QPlainTextEdit):
    """A wrapping cell editor that scrolls when the viewport limits its size."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)


EDITOR_MAX_WIDTH = 700
EDITOR_MAX_HEIGHT = 500
BORDER_COLOR_NORMAL = QColor(GRID)
BORDER_COLOR_SELECTED = QColor(ACCENT)


class MultilineEditDelegate(QStyledItemDelegate):
    """Track the active cell so file operations can commit pending text safely."""
    pendingChanged = pyqtSignal()

    def __init__(self, undo_stack, table_view=None):
        super().__init__(table_view)
        self.undo_stack = undo_stack
        self._table_view = table_view
        self._active_editor = None
        self._state = 'COLLAPSED'
        self._expanded_row = -1
        self._orig_row_height = -1
        self.closeEditor.connect(self._on_close_editor)
        self._destroy_timer = QTimer(self)
        self._destroy_timer.setSingleShot(True)
        self._destroy_timer.timeout.connect(self._after_editor_destroyed)

    def createEditor(self, parent, option, index):
        editor = _ExpandingEditor(parent)
        editor.setTabChangesFocus(False)
        editor.installEventFilter(self)
        editor.setStyleSheet(
            f"QPlainTextEdit {{ background: {SURFACE}; color: {TEXT}; padding: 6px 8px; "
            f"border: 2px solid {BORDER_COLOR_SELECTED.name()}; "
            "border-radius: 5px; }"
        )
        model = index.model()
        source_index = model.mapToSource(index) if hasattr(model, 'mapToSource') else index
        editor._source_index = QPersistentModelIndex(source_index)
        editor._old_value = str(index.data(Qt.EditRole) or '')
        editor._initial_text = ''
        self._active_editor = editor
        editor.destroyed.connect(self._editor_destroyed)
        # The timer belongs to the editor; destroying the editor cancels callbacks.
        editor._resize_timer = QTimer(editor)
        editor._resize_timer.setSingleShot(True)
        editor._resize_timer.timeout.connect(lambda: self._expand_editor(editor))
        editor.textChanged.connect(lambda: self._editor_changed(editor))
        return editor

    def setEditorData(self, editor, index):
        editor._old_value = str(index.data(Qt.EditRole) or '')
        previous = editor.blockSignals(True)
        editor.setPlainText(editor._old_value)
        editor.blockSignals(previous)
        # QTextDocument normalizes CRLF. Merely opening an editor must not edit it.
        editor._initial_text = editor.toPlainText()
        cursor = editor.textCursor()
        cursor.movePosition(cursor.End)
        editor.setTextCursor(cursor)
        self.pendingChanged.emit()

    def has_pending_changes(self):
        editor = self.active_editor()
        return editor is not None and editor.toPlainText() != editor._initial_text

    def active_editor(self):
        editor = self._active_editor
        if editor is not None and sip.isdeleted(editor):
            self._active_editor = None
            self._collapse_row()
            return None
        return editor

    def _editor_destroyed(self):
        if self._active_editor is not None and sip.isdeleted(self._active_editor):
            self._active_editor = None
            # Do not relayout the view while Qt is destroying its editor widget.
            self._destroy_timer.start(0)

    def _after_editor_destroyed(self):
        if self.active_editor() is None:
            self._collapse_row()
            self.pendingChanged.emit()

    def commit_active_editor(self):
        editor = self.active_editor()
        if editor is not None:
            self.commitData.emit(editor)
            if not sip.isdeleted(editor):
                self.closeEditor.emit(editor, QStyledItemDelegate.NoHint)

    def setModelData(self, editor, model, index):
        src_index = QModelIndex(editor._source_index)
        if not src_index.isValid():
            return
        old_val = str(src_index.data(Qt.EditRole) or '')
        new_val = editor.toPlainText()
        if new_val == editor._initial_text:
            new_val = old_val
        # Restore the visible row before editing a sort key can move the row.
        self._collapse_row()
        if old_val != new_val:
            self.undo_stack.push(CellEditCommand(
                src_index.model(), src_index.row(), src_index.column(), old_val, new_val
            ))
        editor._old_value = new_val
        editor._initial_text = editor.toPlainText()
        self.pendingChanged.emit()

    def updateEditorGeometry(self, editor, option, index):
        view = self._table_view
        if view is not None and self._state == 'COLLAPSED':
            self._expanded_row = index.row()
            self._orig_row_height = view.rowHeight(index.row())
            self._state = 'EXPANDED'
        editor._cell_rect = QRect(option.rect)
        self._expand_editor(editor)
        editor._resize_timer.start(0)

    def _editor_changed(self, editor):
        if editor is self._active_editor:
            editor._resize_timer.start(0)
            self.pendingChanged.emit()

    def _calc_size(self, editor, cell_rect):
        fm = editor.fontMetrics()
        lines = editor.toPlainText().split('\n') or ['']
        margin = editor.contentsMargins()
        pad_h = margin.left() + margin.right() + 28
        pad_v = margin.top() + margin.bottom() + 14
        viewport = editor.parentWidget().rect()
        available_w = max(1, viewport.width())
        available_h = max(1, viewport.height())
        width = min(max(cell_rect.width(), max(fm.horizontalAdvance(line) for line in lines) + pad_h),
                    EDITOR_MAX_WIDTH, available_w)
        content_w = max(1, width - pad_h)
        wrapped = sum(max(1, (fm.horizontalAdvance(line) + content_w - 1) // content_w)
                      for line in lines)
        height = min(max(self._orig_row_height, fm.lineSpacing() * wrapped + pad_v),
                     EDITOR_MAX_HEIGHT, available_h)
        left = min(max(0, cell_rect.left()), max(0, viewport.width() - width))
        top = min(max(0, cell_rect.top()), max(0, viewport.height() - height))
        return QRect(left, top, int(width), int(height))

    def _expand_editor(self, editor):
        if (sip.isdeleted(editor) or editor is not self.active_editor()
                or not hasattr(editor, '_cell_rect')):
            return
        rect = self._calc_size(editor, editor._cell_rect)
        view = self._table_view
        if self._state == 'EXPANDED' and view is not None and self._expanded_row >= 0:
            if view.rowHeight(self._expanded_row) != rect.height():
                view.setRowHeight(self._expanded_row, rect.height())
        editor.setGeometry(rect)

    def eventFilter(self, editor, event):
        if event.type() == QEvent.ShortcutOverride:
            if (event.modifiers() & Qt.ControlModifier and event.key() in
                    (Qt.Key_C, Qt.Key_X, Qt.Key_V, Qt.Key_Z, Qt.Key_Y)) or event.key() == Qt.Key_Delete:
                event.accept()
                return True
        if event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Tab and not event.modifiers():
                editor.insertPlainText('\n')
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
                self.commitData.emit(editor)
                self.closeEditor.emit(editor, QStyledItemDelegate.NoHint)
                return True
            if event.key() == Qt.Key_Escape:
                self.closeEditor.emit(editor, QStyledItemDelegate.RevertModelCache)
                return True
        return super().eventFilter(editor, event)

    def _on_close_editor(self, editor, hint):
        if editor is self._active_editor:
            self._active_editor = None
        self._collapse_row()
        self.pendingChanged.emit()

    def _collapse_row(self):
        if (self._state == 'EXPANDED' and self._table_view is not None
                and not sip.isdeleted(self._table_view)
                and self._table_view.model() is not None
                and 0 <= self._expanded_row < self._table_view.model().rowCount()
                and self._orig_row_height > 0):
            self._table_view.setRowHeight(self._expanded_row, self._orig_row_height)
        self._state = 'COLLAPSED'
        self._expanded_row = -1
        self._orig_row_height = -1

    def destroyEditor(self, editor, index):
        if editor is self._active_editor:
            self._active_editor = None
        self._collapse_row()
        self.pendingChanged.emit()
        super().destroyEditor(editor, index)

    def paint(self, painter, option, index):
        """Readable one-line previews, quiet grid lines and a clear active cell."""
        text = index.data(Qt.DisplayRole)
        rect = option.rect
        is_selected = bool(option.state & QStyle.State_Selected)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, False)

        if is_selected:
            painter.fillRect(rect, QColor(SELECTION))
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(rect, QColor('#f0f5fd'))
        elif option.features & QStyleOptionViewItem.Alternate:
            painter.fillRect(rect, QColor(ALTERNATE_ROW))
        else:
            painter.fillRect(rect, QColor(SURFACE))

        painter.setFont(option.font)
        painter.setPen(QColor('#214b91' if is_selected else TEXT))

        if text is not None:
            text_rect = rect.adjusted(10, 0, -10, 0)
            preview = str(text).replace('\r\n', '\n').replace('\r', '\n').replace('\n', '  [换行]  ')
            preview = option.fontMetrics.elidedText(preview, Qt.ElideRight, max(0, text_rect.width()))
            painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, preview)

        painter.setPen(BORDER_COLOR_NORMAL)
        painter.drawLine(rect.right(), rect.top(), rect.right(), rect.bottom())
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        if option.state & QStyle.State_HasFocus:
            painter.setPen(QPen(BORDER_COLOR_SELECTED, 1.5))
            painter.drawRect(rect.adjusted(1, 1, -1, -1))

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


class SaveSettingsDialog(QDialog):
    """Choose the BOM policy used by subsequent Save and Save As actions."""

    def __init__(self, write_bom=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("保存设置")
        self.setMinimumWidth(420)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        layout = QVBoxLayout(self)
        self.bom_checkbox = QCheckBox("保存时写入 BOM")
        self.bom_checkbox.setChecked(bool(write_bom))
        layout.addWidget(self.bom_checkbox)
        hint = QLabel("适用于 UTF-8、UTF-16 和 UTF-32；GBK 等编码不会添加 BOM。\n"
                      "此选项对之后的保存和另存为生效。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.button(QDialogButtonBox.Ok).setText("确定")
        self.button_box.button(QDialogButtonBox.Cancel).setText("取消")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)


# ──────────────────────── Line Number Header ──────────────────
class RowNumberHeaderView(QHeaderView):
    def __init__(self, parent=None):
        super().__init__(Qt.Vertical, parent)

    def paintSection(self, painter, rect, logicalIndex):
        painter.save()
        painter.fillRect(rect, QColor('#f5f8fc'))
        painter.setPen(QColor(MUTED))
        painter.drawText(rect, Qt.AlignCenter, str(logicalIndex + 1))
        painter.setPen(QColor(GRID))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        painter.drawLine(rect.right(), rect.top(), rect.right(), rect.bottom())
        painter.restore()


# ──────────────────────── Main Window ─────────────────────────
class CsvEditorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Meng_CSVEditor")
        self.resize(1200, 800)
        self.setMinimumSize(320, 200)
        self.setWindowIcon(make_icon('app'))

        self._filepath = None
        self._encoding = 'utf-8'
        self._csv_format = CsvFormat()
        self._write_bom = self._read_bom_preference()

        # Undo stack（业务层状态管理）
        self.undo_stack = SafeUndoStack(self)
        self.undo_stack.cleanChanged.connect(self._on_clean_changed)

        # 业务层 Model
        self.model = CsvTableModel(self)

        # 排序代理
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)

        # 表格视图
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(-1, Qt.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionsMovable(True)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setObjectName('csvTable')
        self.table.setMouseTracking(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setDefaultSectionSize(180)
        self.table.horizontalHeader().setFixedHeight(34)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        # 行号表头
        self.row_header = RowNumberHeaderView(self.table)
        self.table.setVerticalHeader(self.row_header)
        self.row_header.setDefaultSectionSize(36)
        self.row_header.setMinimumSectionSize(28)
        self.row_header.setMinimumWidth(40)

        # 多行编辑委托（Tab 换行功能在此）
        self.delegate = MultilineEditDelegate(self.undo_stack, self.table)
        self.table.setItemDelegate(self.delegate)
        self.delegate.pendingChanged.connect(self._update_title)

        self._build_workspace()

        # 状态栏
        self.status_bar = QStatusBar()
        self.status_bar.setSizeGripEnabled(False)
        self.setStatusBar(self.status_bar)
        self.lbl_position = QLabel("就绪")
        self.lbl_size = QLabel("")
        self.lbl_position.setObjectName('statusPosition')
        self.lbl_size.setObjectName('statusMeta')
        self.status_bar.addWidget(self.lbl_position, 1)
        self.status_bar.addPermanentWidget(self.lbl_size)
        self.model.operationFailed.connect(self._on_operation_failed, Qt.QueuedConnection)

        self.table.selectionModel().currentChanged.connect(self._update_status)
        for signal in (self.model.rowsInserted, self.model.rowsRemoved,
                       self.model.columnsInserted, self.model.columnsRemoved,
                       self.model.modelReset):
            signal.connect(lambda *args: self._update_size_label())

        self._build_menus()
        self._build_toolbar()

        self.find_dialog = None
        self.replace_dialog = None

        self._new_file()

    def _build_workspace(self):
        workspace = QWidget()
        workspace.setObjectName('workspace')
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        self.document_header = QFrame()
        self.document_header.setObjectName('documentHeader')
        header = QHBoxLayout(self.document_header)
        header.setContentsMargins(16, 12, 16, 12)
        header.setSpacing(12)
        document_icon = QLabel()
        document_icon.setObjectName('documentIcon')
        document_icon.setFixedSize(40, 40)
        document_icon.setAlignment(Qt.AlignCenter)
        document_icon.setPixmap(make_icon('grid').pixmap(24, 24))
        header.addWidget(document_icon)
        document_details = QVBoxLayout()
        document_details.setSpacing(4)
        self.lbl_document_name = ElidedLabel()
        self.lbl_document_name.setObjectName('documentTitle')
        self.lbl_document_path = ElidedLabel()
        self.lbl_document_path.setObjectName('documentPath')
        document_details.addWidget(self.lbl_document_name)
        document_details.addWidget(self.lbl_document_path)
        header.addLayout(document_details, 1)
        self.lbl_document_state = QLabel()
        self.lbl_document_state.setObjectName('documentMeta')
        header.addWidget(self.lbl_document_state)
        layout.addWidget(self.document_header)
        card = QFrame()
        card.setObjectName('tableCard')
        table_layout = QVBoxLayout(card)
        table_layout.setContentsMargins(1, 1, 1, 1)
        table_layout.setSpacing(0)
        table_layout.addWidget(self.table)
        layout.addWidget(card, 1)
        self.setCentralWidget(workspace)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'document_header'):
            self.document_header.setVisible(self.height() >= 420)
            margin = 12 if self.width() < 760 else 18
            self.centralWidget().layout().setContentsMargins(margin, 12 if margin == 12 else 16, margin,
                                                          12 if margin == 12 else 16)
        if hasattr(self, '_toolbar_buttons'):
            compact = self.width() < 760
            for button, primary in self._toolbar_buttons:
                button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if primary or not compact
                                          else Qt.ToolButtonIconOnly)
            self.toolbar_brand_action.setVisible(self.width() >= 680)
            self.toolbar_hint_action.setVisible(self.width() >= 1150)

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
        self.act_undo = QAction("撤销(&U)", self, triggered=self._undo)
        self.act_undo.setShortcut(QKeySequence.Undo)
        self.act_redo = QAction("重做(&R)", self, triggered=self._redo)
        self.act_redo.setShortcut(QKeySequence.Redo)
        self.undo_stack.canUndoChanged.connect(self._update_history_actions)
        self.undo_stack.canRedoChanged.connect(self._update_history_actions)
        self.delegate.pendingChanged.connect(self._update_history_actions)
        self._update_history_actions()
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

        settings_menu = menubar.addMenu("设置(&T)")
        self.act_save_settings = QAction("保存设置...", self, triggered=self._show_save_settings)
        settings_menu.addAction(self.act_save_settings)

        help_menu = menubar.addMenu("帮助(&H)")
        help_menu.addAction("关于", self._show_about)

        action_icons = (
            (self.act_new, 'new', '新建', '新建文件 (Ctrl+N)'),
            (self.act_open, 'open', '打开', '打开文件 (Ctrl+O)'),
            (self.act_save, 'save', '保存', '保存文件 (Ctrl+S)'),
            (self.act_save_as, 'save_as', '另存为', '另存为 (Ctrl+Shift+S)'),
            (self.act_undo, 'undo', '撤销', '撤销 (Ctrl+Z)'),
            (self.act_redo, 'redo', '重做', '重做 (Ctrl+Y)'),
            (self.act_find, 'find', '查找', '查找 (Ctrl+F)'),
            (self.act_replace, 'replace', '替换', '替换 (Ctrl+H)'),
            (self.act_save_settings, 'settings', '设置', '保存设置与 BOM 开关'),
            (self.act_ins_row, 'row', '插入行', '在上方插入行 (Ctrl+I)'),
            (self.act_ins_col, 'column', '插入列', '在左侧插入列 (Ctrl+J)'),
            (self.act_del_row, 'delete', '删除行', '删除行 (Ctrl+D)'),
            (self.act_del_col, 'delete', '删除列', '删除列 (Ctrl+Shift+D)'),
        )
        for action, icon, text, tooltip in action_icons:
            action.setIcon(make_icon(icon))
            action.setIconText(text)
            action.setToolTip(tooltip)

    def _build_toolbar(self):
        tb = QToolBar("主工具栏")
        tb.setObjectName('mainToolbar')
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setIconSize(QSize(20, 20))
        self.addToolBar(tb)
        self.toolbar_brand = QLabel('MENG CSV')
        self.toolbar_brand.setObjectName('brandLabel')
        self.toolbar_brand_action = tb.addWidget(self.toolbar_brand)
        tb.addSeparator()
        self._toolbar_buttons = []

        def add_button(action, primary=False):
            tb.addAction(action)
            button = tb.widgetForAction(action)
            button.setIconSize(QSize(18, 18))
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            if primary:
                button.setObjectName('primaryAction')
                button.setIcon(make_icon('save', '#ffffff'))
            button.setAccessibleName(action.iconText())
            self._toolbar_buttons.append((button, primary))

        add_button(self.act_new)
        add_button(self.act_open)
        add_button(self.act_save, primary=True)
        tb.addSeparator()
        add_button(self.act_undo)
        add_button(self.act_redo)
        tb.addSeparator()
        add_button(self.act_find)
        add_button(self.act_replace)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.toolbar_hint = QLabel('Tab 换行  ·  Ctrl+Enter 提交')
        self.toolbar_hint.setObjectName('toolbarHint')
        self.toolbar_hint_action = tb.addWidget(self.toolbar_hint)
        add_button(self.act_save_settings)

    # ────────── File operations ──────────
    def _new_file(self):
        if not self._confirm_discard():
            return
        self.model.load_data([[''] * 5, [''] * 5])
        self._filepath = None
        self._csv_format = CsvFormat()
        self._encoding = self._csv_format.encoding
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
        if path:
            self._load_file(path)

    def _load_file(self, path):
        try:
            rows, csv_format = read_csv_document(path)
            # load_data prepares the replacement before resetting the live model.
            # A failed allocation must leave the pending editor and history intact.
            self.model.load_data(rows, column_count=max(1, max((len(row) for row in rows), default=0)))
        except Exception as error:
            QMessageBox.critical(self, "错误", f"无法打开文件:\n{error}")
            return False
        self._filepath = path
        self._csv_format = csv_format
        self._encoding = csv_format.encoding
        self.undo_stack.clear()
        self.undo_stack.setClean()
        self._update_title()
        self._update_size_label()
        self._add_recent(path)
        return True

    def _write_document(self, path, csv_format):
        try:
            csv_format = csv_format_for_save(csv_format, write_bom=self._write_bom)
            write_csv(path, self.model.get_all_data(), csv_format.encoding,
                      csv_format=csv_format, write_bom=self._write_bom)
        except Exception as error:
            QMessageBox.critical(self, "错误", f"保存失败:\n{error}")
            return False
        self._filepath = path
        self._csv_format = csv_format
        self._encoding = csv_format.encoding
        self.undo_stack.setClean()
        self._update_title()
        self._update_size_label()
        self._add_recent(path)
        return True

    def _save_file(self):
        self.delegate.commit_active_editor()
        if not self._filepath:
            return self._save_as()
        return self._write_document(self._filepath, self._csv_format)

    def _save_as(self):
        self.delegate.commit_active_editor()
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为", self._filepath or "",
            "CSV 文件 (*.csv);;TSV 文件 (*.tsv);;所有文件 (*)"
        )
        if not path:
            return False
        extension = os.path.splitext(path)[1].lower()
        delimiter = '\t' if extension == '.tsv' else ',' if extension == '.csv' else self._csv_format.delimiter
        return self._write_document(path, replace(self._csv_format, delimiter=delimiter))

    def _confirm_discard(self):
        self.delegate.commit_active_editor()
        if self.undo_stack.isClean():
            return True
        response = QMessageBox.question(
            self, "未保存的更改", "当前文件有未保存的更改，是否保存？",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save
        )
        if response == QMessageBox.Save:
            return self._save_file()
        return response == QMessageBox.Discard

    def _update_title(self):
        name = os.path.basename(self._filepath) if self._filepath else "未命名"
        pending = hasattr(self, 'delegate') and self.delegate.has_pending_changes()
        is_dirty = pending or not self.undo_stack.isClean()
        dirty = " *" if is_dirty else ""
        self.setWindowTitle(f"{name}{dirty} - Meng_CSVEditor")
        if hasattr(self, 'lbl_document_name'):
            self.lbl_document_name.set_full_text(name if self._filepath else '未命名文档')
            self.lbl_document_path.set_full_text(
                os.path.abspath(self._filepath) if self._filepath else '新建 CSV · 打开文件或直接开始编辑')
            self.lbl_document_state.setText('未保存更改' if is_dirty else '已保存' if self._filepath else '新文档')
            self.lbl_document_state.setProperty('dirty', is_dirty)
            self.lbl_document_state.style().unpolish(self.lbl_document_state)
            self.lbl_document_state.style().polish(self.lbl_document_state)

    def _update_size_label(self):
        r = self.model.rowCount()
        c = self.model.columnCount()
        if not self.table.currentIndex().isValid():
            self.lbl_position.setText("就绪")
        delimiter = self._csv_format.delimiter
        label = "TSV" if delimiter == '\t' else "CSV" if delimiter == ',' else f"分隔符 {delimiter!r}"
        bom_label = "BOM 开" if self._write_bom else "BOM 关"
        self.lbl_size.setText(f"{r} 行 x {c} 列 | {self._encoding} | {label} | {bom_label}")

    def _show_save_settings(self):
        dialog = SaveSettingsDialog(self._write_bom, self)
        if dialog.exec_() == QDialog.Accepted:
            self._set_bom_preference(dialog.bom_checkbox.isChecked())
        dialog.deleteLater()

    def _set_bom_preference(self, write_bom):
        self._write_bom = bool(write_bom)
        QSettings("MengCSVEditor", "MengCSVEditor").setValue('write_bom', self._write_bom)
        self._update_size_label()

    @staticmethod
    def _read_bom_preference():
        try:
            value = QSettings("MengCSVEditor", "MengCSVEditor").value(
                'write_bom', False)
        except (TypeError, ValueError, OverflowError):
            return False
        # Qt's bool conversion treats arbitrary nonempty strings as true.
        # Accept only known persisted boolean forms; corrupt values default off.
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            return value.strip().casefold() in ('true', '1')
        return False

    def _update_history_actions(self, *args):
        pending = self.delegate.has_pending_changes()
        self.act_undo.setEnabled(pending or self.undo_stack.canUndo())
        self.act_redo.setEnabled(not pending and self.undo_stack.canRedo())

    def _undo(self):
        self.delegate.commit_active_editor()
        self._apply_history(self.undo_stack.undo)

    def _redo(self):
        self.delegate.commit_active_editor()
        self._apply_history(self.undo_stack.redo)

    def _apply_history(self, operation):
        dynamic = self.proxy.dynamicSortFilter()
        self.proxy.setDynamicSortFilter(False)
        try:
            operation()
        finally:
            self.proxy.setDynamicSortFilter(dynamic)

    def _on_clean_changed(self, clean):
        self._update_title()

    def _update_status(self, current, previous):
        if current.isValid():
            src = self.proxy.mapToSource(current)
            self.lbl_position.setText(
                f"行: {src.row()+1}  列: {CsvTableModel._col_letter(src.column())} ({src.column()+1})"
            )
        else:
            self.lbl_position.setText("就绪")

    def _on_operation_failed(self, message):
        self.status_bar.showMessage(f"操作未完成：{message}", 8000)

    # ────────── Recent files ──────────
    def _load_recent(self):
        self.recent_menu.clear()
        for f in self._read_recent_files():
            try:
                exists = os.path.isfile(f)
            except (OSError, ValueError):
                exists = False
            if exists:
                act = self.recent_menu.addAction(f)
                act.setData(f)
                act.triggered.connect(lambda checked, path=f: self._open_recent(path))
        if not self.recent_menu.actions():
            self.recent_menu.addAction("(空)").setEnabled(False)

    @staticmethod
    def _read_recent_files():
        try:
            raw = QSettings("MengCSVEditor", "MengCSVEditor").value("recent_files", [])
        except (TypeError, ValueError, OverflowError):
            return []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, (list, tuple)):
            return []
        files = []
        for path in raw:
            if isinstance(path, str) and path and '\x00' not in path and path not in files:
                files.append(path)
                if len(files) == 10:
                    break
        return files

    def _add_recent(self, path):
        settings = QSettings("MengCSVEditor", "MengCSVEditor")
        path = os.fsdecode(os.fspath(path))
        files = self._read_recent_files()
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
        try:
            menu.exec_(self.table.viewport().mapToGlobal(pos))
        finally:
            menu.deleteLater()

    # ────────── Row/Col operations (undo-aware) ──────────
    def _current_source_index(self):
        self.delegate.commit_active_editor()
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
    def _visible_columns(self):
        header = self.table.horizontalHeader()
        return [header.logicalIndex(visual) for visual in range(self.model.columnCount())]

    def _copy(self):
        editor = self.delegate.active_editor()
        if editor is not None:
            editor.copy()
            return True
        selected = self.table.selectionModel().selectedIndexes()
        if not selected:
            return False
        cells = {(index.row(), index.column()) for index in selected}
        rows = [index.row() for index in selected]
        header = self.table.horizontalHeader()
        positions = [header.visualIndex(index.column()) for index in selected]
        columns = self._visible_columns()[min(positions):max(positions) + 1]
        block = [[str(self.proxy.index(row, col).data(Qt.EditRole) or '')
                  if (row, col) in cells else '' for col in columns]
                 for row in range(min(rows), max(rows) + 1)]
        buffer = io.StringIO(newline='')
        csv.writer(buffer, delimiter='\t', lineterminator='\r\n').writerows(block)
        QApplication.clipboard().setText(buffer.getvalue())
        return True

    def _cut(self):
        editor = self.delegate.active_editor()
        if editor is not None:
            editor.cut()
        elif self._copy():
            self._delete_selection()

    def _paste(self):
        editor = self.delegate.active_editor()
        if editor is not None:
            editor.paste()
            return
        text = QApplication.clipboard().text()
        if not text:
            return
        try:
            self._paste_table_text(text)
        except csv.Error as error:
            QMessageBox.warning(self, "粘贴失败", f"剪贴板中的表格格式无效:\n{error}")
        except (MemoryError, OverflowError):
            QMessageBox.warning(self, "粘贴失败", "剪贴板中的表格过大，内存不足，无法粘贴。")

    def _paste_table_text(self, text):
        block = parse_csv_text(text, delimiter='\t', strict=True)
        if not block:
            return
        width = max(1, max(len(row) for row in block))
        block = [row + [''] * (width - len(row)) for row in block]
        current = self.table.currentIndex()
        start_row = current.row() if current.isValid() else 0
        visible_columns = self._visible_columns()
        start_column = visible_columns.index(current.column()) if current.isValid() else 0
        original_rows = self.model.rowCount()
        original_cols = self.model.columnCount()
        # Snapshot source identities before sort keys or dimensions change.
        target_row_indices = []
        for offset in range(len(block)):
            visible_row = start_row + offset
            if visible_row < original_rows and original_cols:
                target_row_indices.append(self.proxy.mapToSource(self.proxy.index(visible_row, 0)).row())
            else:
                target_row_indices.append(visible_row)
        target_cols = [visible_columns[position] if position < len(visible_columns)
                       else original_cols + position - len(visible_columns)
                       for position in range(start_column, start_column + width)]
        cells = []
        for offset, row in enumerate(block):
            source_row = target_row_indices[offset]
            for column_offset, value in enumerate(row):
                source_col = target_cols[column_offset]
                old = self.model.data(self.model.index(source_row, source_col), Qt.EditRole)
                old = str(old or '') if source_row < original_rows and source_col < original_cols else ''
                cells.append((source_row, source_col, old, value))
        new_rows = max(original_rows, start_row + len(block))
        new_cols = max(original_cols, start_column + width)
        if new_rows == original_rows and new_cols == original_cols and all(old == new for _, _, old, new in cells):
            return
        self._push_command(PasteCellsCommand(self.model, cells, new_rows, new_cols))

    def _push_command(self, command):
        dynamic = self.proxy.dynamicSortFilter()
        self.proxy.setDynamicSortFilter(False)
        try:
            self.undo_stack.push(command)
        finally:
            self.proxy.setDynamicSortFilter(dynamic)

    def _delete_selection(self):
        self.delegate.commit_active_editor()
        selected = self.table.selectionModel().selectedIndexes()
        cells = []
        for index in selected:
            source = self.proxy.mapToSource(index)
            value = str(source.data(Qt.EditRole) or '')
            if value:
                cells.append((source.row(), source.column(), value))
        if cells:
            self._push_command(ClearSelectionCommand(self.model, cells))

    # ────────── Find / Replace ──────────
    def _show_find(self):
        if self.find_dialog is None or sip.isdeleted(self.find_dialog):
            self.find_dialog = FindReplaceDialog(self, show_replace=False)
            self.find_dialog.find_next.connect(self._do_find)
        self.find_dialog.show()
        self.find_dialog.raise_()
        self.find_dialog.activateWindow()

    def _show_replace(self):
        if self.replace_dialog is None or sip.isdeleted(self.replace_dialog):
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
        self.delegate.commit_active_editor()
        current = self.table.currentIndex()
        columns = ([current.column()] if col_only and current.isValid()
                   else self._visible_columns()[:1] if col_only else self._visible_columns())
        column_count = len(columns)
        cell_count = self.proxy.rowCount() * column_count
        if not cell_count:
            return
        start = ((current.row() * column_count + columns.index(current.column()) + 1) % cell_count
                 if current.isValid() and current.column() in columns else 0)
        needle = text if case_sensitive else text.casefold()
        for offset in range(cell_count):
            row, column_offset = divmod((start + offset) % cell_count, column_count)
            column = columns[column_offset]
            index = self.proxy.index(row, column)
            value = str(index.data(Qt.EditRole) or '')
            if needle in (value if case_sensitive else value.casefold()):
                self.table.setCurrentIndex(index)
                self.table.scrollTo(index)
                self._set_find_status("", "")
                return
        self._set_find_status("未找到匹配项", "orange")

    def _do_replace(self, find_text, repl_text, case_sensitive, col_only):
        if not find_text:
            return
        source = self._current_source_index()
        if source.isValid():
            old = str(source.data(Qt.EditRole) or '')
            new = (old.replace(find_text, repl_text) if case_sensitive
                   else self._case_insensitive_replace(old, find_text, repl_text))
            if new != old:
                self._push_command(CellEditCommand(
                    self.model, source.row(), source.column(), old, new
                ))
        self._do_find(find_text, case_sensitive, col_only)

    def _do_replace_all(self, find_text, repl_text, case_sensitive, col_only):
        if not find_text:
            return
        self.delegate.commit_active_editor()
        current = self.table.currentIndex()
        columns = ([current.column()] if col_only and current.isValid()
                   else self._visible_columns()[:1] if col_only else self._visible_columns())
        cells = []
        for row in range(self.model.rowCount()):
            for column in columns:
                index = self.model.index(row, column)
                old = str(index.data(Qt.EditRole) or '')
                new = (old.replace(find_text, repl_text) if case_sensitive
                       else self._case_insensitive_replace(old, find_text, repl_text))
                if new != old:
                    cells.append((row, column, old, new))
        if cells:
            self._push_command(BatchEditCommand(self.model, cells, "全部替换"))
            self._set_find_status(f"已替换 {len(cells)} 个单元格", "green")
        else:
            self._set_find_status("未找到匹配项", "orange")

    @staticmethod
    def _case_insensitive_replace(text, find, repl):
        if not find:
            return text
        return re.sub(re.escape(find), lambda match: repl, text, flags=re.IGNORECASE)

    def _set_find_status(self, msg, color):
        sender = self.sender()
        if isinstance(sender, FindReplaceDialog):
            dialogs = [sender]
        else:
            dialogs = [dialog for dialog in (self.find_dialog, self.replace_dialog)
                       if dialog is not None and not sip.isdeleted(dialog) and dialog.isVisible()]
        for dialog in dialogs:
            dialog.status_label.setText(msg)
            dialog.status_label.setStyleSheet(f"color: {color};" if color else "")

    # ────────── About ──────────
    def _show_about(self):
        QMessageBox.about(
            self, "关于 Meng_CSVEditor",
            "Meng_CSVEditor\n"
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
    if not QApplication.instance():
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Meng_CSVEditor")
    from crash_reporter import install_exception_handler
    install_exception_handler(app)
    apply_theme(app)

    window = CsvEditorWindow()

    arguments = [argument for argument in sys.argv[1:] if argument != '--smoke-test']
    if arguments and os.path.isfile(arguments[0]):
        window._load_file(arguments[0])

    window.show()
    if '--smoke-test' in sys.argv[1:]:
        QTimer.singleShot(250, app.quit)
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())
