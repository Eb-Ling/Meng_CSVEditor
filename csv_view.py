"""Present CSV record 1 as editable headings while retaining file coordinates."""
from PyQt5.QtCore import QSortFilterProxyModel, Qt, QVariant


class CsvViewProxy(QSortFilterProxyModel):
    """Only hide record 1; sorting is deliberately unsupported."""
    def filterAcceptsRow(self, source_row, source_parent):
        return source_row > 0

    def sort(self, column, order=Qt.AscendingOrder):
        # QTableView.sortByColumn can call this even with sorting disabled.
        # Keep source order for every caller, including programmatic calls.
        return

    def setSourceModel(self, model):
        previous = self.sourceModel()
        if previous is not None:
            previous.dataChanged.disconnect(self._source_changed)
            previous.rowsInserted.disconnect(self._rows_changed)
            previous.rowsRemoved.disconnect(self._rows_changed)
        super().setSourceModel(model)
        if model is not None:
            model.dataChanged.connect(self._source_changed)
            model.rowsInserted.connect(self._rows_changed)
            model.rowsRemoved.connect(self._rows_changed)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        source = self.sourceModel()
        if orientation != Qt.Horizontal:
            return super().headerData(section, orientation, role)
        if (source is None or not source._valid_number(section)
                or section >= source.columnCount()):
            return QVariant()
        value = str(source.data(source.index(0, section), Qt.EditRole) or '')
        if role == Qt.DisplayRole:
            return value.replace('\r\n', '\n').replace('\r', '\n').replace('\n', ' [换行] ')[:2000] or '（空抬头）'
        if role == Qt.EditRole:
            return value
        if role == Qt.ToolTipRole:
            return f'CSV 第 1 行 · 第 {section + 1} 列 · 右键编辑抬头\n' + value[:2000]
        return QVariant()

    def _source_changed(self, first, last, roles=None):
        if first.row() == 0:
            self.headerDataChanged.emit(Qt.Horizontal, first.column(), last.column())

    def _rows_changed(self, parent, first, last):
        if first == 0 and self.columnCount():
            self.headerDataChanged.emit(Qt.Horizontal, 0, self.columnCount() - 1)
