# -*- coding: utf-8 -*-
"""
CSV Model Layer - 业务逻辑层
包含 CSV 文件读写、表格数据模型（QAbstractTableModel）
与 GUI 无关，纯粹的数据操作与模型抽象
"""
import csv
from PyQt5.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QVariant
)


# ─────────────────────────── CSV IO ───────────────────────────
def detect_encoding(filepath):
    """自动检测文件编码，精确识别 BOM，优先常见中文编码"""
    # 先检查文件是否以 BOM 开头
    with open(filepath, 'rb') as f:
        raw = f.read(4)
    if raw.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'
    # 无 BOM，依次尝试其他编码
    for enc in ('utf-8', 'gbk', 'gb2312', 'latin-1'):
        try:
            with open(filepath, 'r', encoding=enc) as f:
                f.read(4096)
            return enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return 'utf-8'


def read_csv(filepath):
    """读取 CSV 文件，返回 (rows, encoding)；rows 为二维列表且列数对齐"""
    enc = detect_encoding(filepath)
    rows = []
    with open(filepath, 'r', encoding=enc, newline='') as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(row)
    # 对齐列数
    max_cols = max((len(r) for r in rows), default=0)
    for r in rows:
        while len(r) < max_cols:
            r.append('')
    return rows, enc


def write_csv(filepath, rows, encoding='utf-8'):
    """将二维列表写入 CSV 文件"""
    with open(filepath, 'w', encoding=encoding, newline='') as f:
        writer = csv.writer(f)
        writer.writerows(rows)


# ─────────────────────── Table Model ──────────────────────────
class CsvTableModel(QAbstractTableModel):
    """CSV 数据的 Qt 表格模型，负责数据存取与结构操作，不含任何 GUI 逻辑"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = []       # list of list of str
        self._headers = []    # column header labels

    # ── 基础查询 ──
    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        if self._data:
            return len(self._data[0])
        return 0

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        r, c = index.row(), index.column()
        if role in (Qt.DisplayRole, Qt.EditRole):
            if 0 <= r < len(self._data) and 0 <= c < len(self._data[r]):
                return self._data[r][c]
        return QVariant()

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid() or role != Qt.EditRole:
            return False
        r, c = index.row(), index.column()
        if 0 <= r < len(self._data) and 0 <= c < len(self._data[r]):
            self._data[r][c] = str(value)
            self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
            return True
        return False

    def _set_data(self, row, col, value):
        """直接写入数据（供 Undo 命令使用，不触发额外 Undo 记录）"""
        if 0 <= row < len(self._data) and 0 <= col < len(self._data[row]):
            self._data[row][col] = str(value)
            idx = self.index(row, col)
            self.dataChanged.emit(idx, idx, [Qt.DisplayRole, Qt.EditRole])

    def flags(self, index):
        return Qt.ItemIsSelectable | Qt.ItemIsEditable | Qt.ItemIsEnabled

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            if section < len(self._headers):
                return self._headers[section]
            return self._col_letter(section)
        return str(section + 1)

    # ── 数据加载与导出 ──
    def load_data(self, rows):
        self.beginResetModel()
        self._data = [list(r) for r in rows]
        self._headers = [self._col_letter(i) for i in range(self.columnCount())]
        self.endResetModel()

    def get_all_data(self):
        return [list(r) for r in self._data]

    def get_block(self, start_row, start_col, rows, cols):
        """获取指定区域的数据块"""
        block = []
        for i in range(rows):
            row_data = []
            for j in range(cols):
                r, c = start_row + i, start_col + j
                if 0 <= r < len(self._data) and 0 <= c < len(self._data[r]):
                    row_data.append(self._data[r][c])
                else:
                    row_data.append('')
            block.append(row_data)
        return block

    # ── 行列结构操作 ──
    def insertRows(self, row, count, parent=QModelIndex()):
        self.beginInsertRows(parent, row, row + count - 1)
        ncols = self.columnCount() or 1
        for _ in range(count):
            self._data.insert(row, [''] * ncols)
        self.endInsertRows()
        return True

    def removeRows(self, row, count, parent=QModelIndex()):
        if row < 0 or row + count > len(self._data):
            return False
        self.beginRemoveRows(parent, row, row + count - 1)
        del self._data[row:row + count]
        self.endRemoveRows()
        return True

    def insertColumns(self, col, count, parent=QModelIndex()):
        self.beginInsertColumns(parent, col, col + count - 1)
        for r in self._data:
            for _ in range(count):
                r.insert(col, '')
        self._headers = [self._col_letter(i) for i in range(self.columnCount())]
        self.endInsertColumns()
        return True

    def removeColumns(self, col, count, parent=QModelIndex()):
        if not self._data:
            return False
        if col < 0 or col + count > len(self._data[0]):
            return False
        self.beginRemoveColumns(parent, col, col + count - 1)
        for r in self._data:
            del r[col:col + count]
        self._headers = [self._col_letter(i) for i in range(self.columnCount())]
        self.endRemoveColumns()
        return True

    # ── 工具方法 ──
    @staticmethod
    def _col_letter(col):
        """将列索引转换为 A, B, ..., Z, AA, AB, ... 格式"""
        result = ''
        col += 1
        while col > 0:
            col -= 1
            result = chr(ord('A') + col % 26) + result
            col //= 26
        return result
