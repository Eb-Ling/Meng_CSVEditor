# -*- coding: utf-8 -*-
"""Undo commands for cell edits and table structure changes."""
from PyQt5 import sip
from PyQt5.QtCore import QSignalBlocker
from PyQt5.QtWidgets import QAction, QUndoCommand, QUndoStack


class _ModelCommand(QUndoCommand):
    """Keep obsolete commands away from another document or a deleted model."""
    def __init__(self, model, text):
        super().__init__(text)
        self.model = model
        self.generation = getattr(model, '_document_generation', None)
        self.applied = False
        self.ever_applied = False
        self._failure_count = 0

    def _reject(self):
        self.setObsolete(True)
        return False

    def _failed(self, error=None):
        self._failure_count += 1
        # An operation already in history must remain retryable. Qt otherwise
        # removes it and may report clean while its changes are still present.
        if not self.ever_applied:
            self.setObsolete(True)
        if not sip.isdeleted(self.model):
            message = ('内存不足，无法完成操作。请关闭其他文件后重试。'
                       if isinstance(error, MemoryError) else '无法完成操作，表格数据保持不变。')
            self.model.operationFailed.emit(message)
        return False

    def _run(self, shape, operation):
        if self.isObsolete():
            return False
        try:
            if (sip.isdeleted(self.model)
                    or self.generation != self.model._document_generation):
                return self._reject()
            if shape != (self.model.rowCount(), self.model.columnCount()):
                return self._failed() if self.ever_applied else self._reject()
            if not operation():
                return self._failed()
            self.ever_applied = True
            return True
        except Exception as error:
            # Exceptions escaping a Python QUndoCommand virtual method can
            # abort the Qt process. Invalid commands leave the grid untouched.
            return self._failed(error)

    def _shape(self):
        return self.model.rowCount(), self.model.columnCount()


class SafeUndoStack(QUndoStack):
    """Keep model command history and clean state consistent on failed steps."""
    def _state(self):
        return (self.index(), self.isClean(), self.canUndo(), self.canRedo(),
                self.undoText(), self.redoText())

    def _publish(self, before):
        after = self._state()
        signals = (self.indexChanged, self.cleanChanged, self.canUndoChanged,
                   self.canRedoChanged, self.undoTextChanged, self.redoTextChanged)
        for signal, old, new in zip(signals, before, after):
            if old != new:
                signal.emit(new)

    def push(self, command):
        if not isinstance(command, _ModelCommand):
            return super().push(command)
        if command.isObsolete():
            return
        before = self._state()
        blocker = QSignalBlocker(self)
        try:
            # Native push deletes the redo branch even when initial redo
            # fails. Apply first; successful redo is idempotent inside push.
            failures = command._failure_count
            command.redo()
            if (command._failure_count != failures or command.isObsolete()
                    or not command.applied):
                return
            super().push(command)
        finally:
            del blocker
            self._publish(before)

    def _transition(self, undoing):
        before = self._state()
        position = self.index() - 1 if undoing else self.index()
        if position < 0 or position >= self.count():
            return False
        command = self.command(position)
        failures = getattr(command, '_failure_count', None)
        blocker = QSignalBlocker(self)
        try:
            if undoing:
                super().undo()
            else:
                super().redo()
            if failures is not None and command._failure_count != failures:
                # Command.applied stays unchanged on failure. The opposite
                # native step is therefore a no-op for data, restoring only
                # Qt's index without losing the command or its clean marker.
                if undoing:
                    super().redo()
                else:
                    super().undo()
                return False
            return self.index() != before[0]
        finally:
            del blocker
            self._publish(before)

    def undo(self):
        return self._transition(True)

    def redo(self):
        return self._transition(False)

    def setIndex(self, index):
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index <= self.count():
            return
        before = self._state()
        blocker = QSignalBlocker(self)
        try:
            while self.index() != index:
                if not self._transition(self.index() > index):
                    break
        finally:
            del blocker
            self._publish(before)

    def _create_history_action(self, parent, prefix, undoing):
        action = QAction(parent)

        def update(*_args):
            if sip.isdeleted(action):
                return
            text = self.undoText() if undoing else self.redoText()
            action.setText((prefix or ('Undo' if undoing else 'Redo')) + (' ' + text if text else ''))
            action.setEnabled(self.canUndo() if undoing else self.canRedo())

        if undoing:
            self.canUndoChanged.connect(update)
            self.undoTextChanged.connect(update)
            action.triggered.connect(self.undo)
        else:
            self.canRedoChanged.connect(update)
            self.redoTextChanged.connect(update)
            action.triggered.connect(self.redo)
        update()
        return action

    def createUndoAction(self, parent, prefix=''):
        return self._create_history_action(parent, prefix, True)

    def createRedoAction(self, parent, prefix=''):
        return self._create_history_action(parent, prefix, False)


class BatchEditCommand(_ModelCommand):
    """Apply validated source-coordinate cell edits as one undo step."""
    def __init__(self, model, cells, text='Replace all', *, _bounds=None):
        super().__init__(model, text)
        self.cells = []
        self.original_shape = (0, 0)
        self.old_lengths = {}
        try:
            if sip.isdeleted(model):
                self._reject()
                return
            self.original_shape = self._shape()
            bounds = _bounds or self.original_shape
            if not all(model._valid_number(value) for value in bounds):
                self._reject()
                return
            # Duplicate destinations retain the actual initial value and the
            # final requested value, regardless of caller-supplied old values.
            unique = {}
            for row, col, _, new in cells:
                if not (model._valid_number(row) and model._valid_number(col)
                        and row < bounds[0] and col < bounds[1]):
                    self._reject()
                    return
                old = model._data[row][col] if row < self.original_shape[0] and col < self.original_shape[1] else ''
                unique[row, col] = (row, col, old, str(new))
            self.cells = list(unique.values())
            lengths = model.get_row_lengths()
            self.old_lengths = {row: lengths[row] for row, _, _, _ in self.cells if row < len(lengths)}
            if bounds == self.original_shape and all(old == new for _, _, old, new in self.cells):
                self._reject()
        except Exception:
            self._reject()

    def _matches(self, value_offset):
        for cell in self.cells:
            row, col = cell[:2]
            if row < self.model.rowCount() and col < self.model.columnCount():
                if self.model._data[row][col] != cell[value_offset]:
                    return False
            elif cell[value_offset] != '':
                return False
        return True

    def _redo_cells(self):
        return self._matches(2) and self.model._apply_cells(
            (row, col, new) for row, col, _, new in self.cells)

    def _undo_cells(self):
        if not self._matches(3) or not self.model._apply_cells(
                (row, col, old) for row, col, old, _ in self.cells):
            return False
        return self.model._restore_row_lengths(self.old_lengths)

    def redo(self):
        if not self.applied and self._run(self.original_shape, self._redo_cells):
            self.applied = True

    def undo(self):
        if self.applied and self._run(self.original_shape, self._undo_cells):
            self.applied = False


class CellEditCommand(BatchEditCommand):
    def __init__(self, model, row, col, old_val, new_val):
        super().__init__(model, [(row, col, old_val, new_val)], f'Edit ({row},{col})')


class _StructureCommand(_ModelCommand):
    def __init__(self, model, position, count, axis, deleting, text):
        super().__init__(model, text)
        self.position, self.count = position, count
        self.axis, self.deleting = axis, deleting
        self.original_shape = (0, 0)
        self.result_shape = (0, 0)
        self.saved_data, self.old_lengths = [], {}
        try:
            if sip.isdeleted(model):
                self._reject()
                return
            self.original_shape = self._shape()
            size = self.original_shape[axis]
            if (not model._valid_number(position) or not model._valid_number(count)
                    or count == 0 or position > size
                    or (deleting and position + count > size)
                    or (not deleting and not model._valid_number(size + count))):
                self._reject()
                return
            result = list(self.original_shape)
            result[axis] += -count if deleting else count
            self.result_shape = tuple(result)
            self.old_lengths = dict(enumerate(model.get_row_lengths()))
            if deleting:
                if axis == 0:
                    self.saved_data = [values[:] for values in model._data[position:position + count]]
                else:
                    self.saved_data = [values[position:position + count] for values in model._data]
        except Exception:
            self._reject()

    def _change(self, deleting):
        if self.axis == 0:
            operation = self.model.removeRows if deleting else self.model.insertRows
        else:
            operation = self.model.removeColumns if deleting else self.model.insertColumns
        return operation(self.position, self.count)

    def _undo_change(self):
        if not self.deleting:
            if not self._change(True):
                return False
            return self.model._restore_row_lengths(self.old_lengths)
        # Deleted values are prepared with the insertion, avoiding a partly
        # restored result if allocation or a field conversion fails.
        rows, cols = self.original_shape
        try:
            if self.axis == 0:
                data = self.model._data[:self.position] + [values[:] for values in self.saved_data] + self.model._data[self.position:]
            else:
                data = [values[:self.position] + saved + values[self.position:]
                        for values, saved in zip(self.model._data, self.saved_data)]
            lengths = [self.old_lengths[row] for row in range(rows)]
            headers = [self.model._col_letter(col) for col in range(cols)]
        except Exception:
            return False
        if self.axis == 0:
            self.model.beginInsertRows(self.model.index(-1, -1), self.position, self.position + self.count - 1)
        else:
            self.model.beginInsertColumns(self.model.index(-1, -1), self.position, self.position + self.count - 1)
        self.model._data, self.model._row_lengths = data, lengths
        self.model._column_count, self.model._headers = cols, headers
        if self.axis == 0:
            self.model.endInsertRows()
        else:
            self.model.endInsertColumns()
        return True

    def redo(self):
        if not self.applied and self._run(self.original_shape, lambda: self._change(self.deleting)):
            self.applied = True

    def undo(self):
        if self.applied and self._run(self.result_shape, self._undo_change):
            self.applied = False


class InsertRowsCommand(_StructureCommand):
    def __init__(self, model, row, count=1):
        super().__init__(model, row, count, 0, False, f'Insert {count} row(s)')


class DeleteRowsCommand(_StructureCommand):
    def __init__(self, model, row, count, saved_data):
        super().__init__(model, row, count, 0, True, f'Delete {count} row(s)')


class InsertColsCommand(_StructureCommand):
    def __init__(self, model, col, count=1):
        super().__init__(model, col, count, 1, False, f'Insert {count} column(s)')


class DeleteColsCommand(_StructureCommand):
    def __init__(self, model, col, count, saved_data):
        super().__init__(model, col, count, 1, True, f'Delete {count} column(s)')


class PasteCellsCommand(BatchEditCommand):
    """Paste mapped cells and grid growth in one allocation-safe undo step."""
    def __init__(self, model, cells, target_rows, target_cols):
        if (model._valid_number(target_rows) and model._valid_number(target_cols)
                and not sip.isdeleted(model)):
            target = max(model.rowCount(), target_rows), max(model.columnCount(), target_cols)
        else:
            target = target_rows, target_cols
        super().__init__(model, cells, 'Paste', _bounds=target)
        self.target_shape = target
        if not sip.isdeleted(model):
            self.old_lengths = dict(enumerate(model.get_row_lengths()))

    def _redo_paste(self):
        return self._matches(2) and self.model._resize_and_apply(
            *self.target_shape, ((row, col, new) for row, col, _, new in self.cells))

    def _undo_paste(self):
        if not self._matches(3):
            return False
        rows, cols = self.original_shape
        cells = ((row, col, old) for row, col, old, _ in self.cells if row < rows and col < cols)
        return self.model._resize_and_apply(rows, cols, cells, self.old_lengths)

    def redo(self):
        if not self.applied and self._run(self.original_shape, self._redo_paste):
            self.applied = True

    def undo(self):
        if self.applied and self._run(self.target_shape, self._undo_paste):
            self.applied = False


class PasteCommand(PasteCellsCommand):
    """Compatibility wrapper for unsorted rectangular block pastes."""
    def __init__(self, model, start_row, start_col, old_block, new_block):
        if not (model._valid_number(start_row) and model._valid_number(start_col)):
            super().__init__(model, [], -1, -1)
            return
        cells = []
        try:
            new_block = [list(values) for values in new_block]
            for offset, values in enumerate(new_block):
                for col_offset, value in enumerate(values):
                    cells.append((start_row + offset, start_col + col_offset, '', value))
            cols = max((len(row) for row in new_block), default=0)
        except Exception:
            super().__init__(model, [], -1, -1)
            return
        super().__init__(model, cells, start_row + len(new_block), start_col + cols)


class ClearSelectionCommand(BatchEditCommand):
    def __init__(self, model, cells):
        super().__init__(model, ((row, col, old, '') for row, col, old in cells), 'Clear selection')
