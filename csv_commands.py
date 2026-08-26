# -*- coding: utf-8 -*-
"""
CSV Commands Layer - 撤销/重做命令层
所有 QUndoCommand 子类，仅依赖 csv_model，不依赖任何 GUI 组件
"""
from PyQt5.QtWidgets import QUndoCommand


class CellEditCommand(QUndoCommand):
    """单个单元格的编辑操作"""
    def __init__(self, model, row, col, old_val, new_val):
        super().__init__(f"Edit ({row},{col})")
        self.model = model
        self.row, self.col = row, col
        self.old_val, self.new_val = old_val, new_val

    def redo(self):
        self.model._set_data(self.row, self.col, self.new_val)

    def undo(self):
        self.model._set_data(self.row, self.col, self.old_val)


class InsertRowsCommand(QUndoCommand):
    """插入行"""
    def __init__(self, model, row, count=1):
        super().__init__(f"Insert {count} row(s)")
        self.model = model
        self.row = row
        self.count = count

    def redo(self):
        self.model.insertRows(self.row, self.count)

    def undo(self):
        self.model.removeRows(self.row, self.count)


class DeleteRowsCommand(QUndoCommand):
    """删除行（保存数据以支持撤销）"""
    def __init__(self, model, row, count, saved_data):
        super().__init__(f"Delete {count} row(s)")
        self.model = model
        self.row = row
        self.count = count
        self.saved_data = saved_data

    def redo(self):
        self.model.removeRows(self.row, self.count)

    def undo(self):
        self.model.insertRows(self.row, self.count)
        for i, row_data in enumerate(self.saved_data):
            for j, val in enumerate(row_data):
                self.model._set_data(self.row + i, j, val)


class InsertColsCommand(QUndoCommand):
    """插入列"""
    def __init__(self, model, col, count=1):
        super().__init__(f"Insert {count} column(s)")
        self.model = model
        self.col = col
        self.count = count

    def redo(self):
        self.model.insertColumns(self.col, self.count)

    def undo(self):
        self.model.removeColumns(self.col, self.count)


class DeleteColsCommand(QUndoCommand):
    """删除列（保存数据以支持撤销）"""
    def __init__(self, model, col, count, saved_data):
        super().__init__(f"Delete {count} column(s)")
        self.model = model
        self.col = col
        self.count = count
        self.saved_data = saved_data

    def redo(self):
        self.model.removeColumns(self.col, self.count)

    def undo(self):
        self.model.insertColumns(self.col, self.count)
        for row_idx in range(self.model.rowCount()):
            for i, val in enumerate(self.saved_data):
                if row_idx < len(val):
                    self.model._set_data(row_idx, self.col + i, val[row_idx])


class PasteCommand(QUndoCommand):
    """粘贴数据块（记录旧值以支持撤销）"""
    def __init__(self, model, start_row, start_col, old_block, new_block):
        super().__init__("Paste")
        self.model = model
        self.sr, self.sc = start_row, start_col
        self.old_block = old_block
        self.new_block = new_block

    def redo(self):
        for i, row in enumerate(self.new_block):
            for j, val in enumerate(row):
                r, c = self.sr + i, self.sc + j
                if r < self.model.rowCount() and c < self.model.columnCount():
                    self.model._set_data(r, c, val)

    def undo(self):
        for i, row in enumerate(self.old_block):
            for j, val in enumerate(row):
                r, c = self.sr + i, self.sc + j
                if r < self.model.rowCount() and c < self.model.columnCount():
                    self.model._set_data(r, c, val)


class ClearSelectionCommand(QUndoCommand):
    """清空选中区域（记录旧值以支持撤销）"""
    def __init__(self, model, cells):
        super().__init__("Clear selection")
        self.model = model
        self.cells = cells  # list of (row, col, old_val)

    def redo(self):
        for r, c, _ in self.cells:
            self.model._set_data(r, c, '')

    def undo(self):
        for r, c, v in self.cells:
            self.model._set_data(r, c, v)
