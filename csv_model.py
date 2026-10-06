# -*- coding: utf-8 -*-
"""CSV file IO and the editable table model."""
import codecs
import csv
import io
import os
import stat
import sys
import tempfile
import threading
from collections import Counter
from dataclasses import dataclass, replace
from itertools import islice

from PyQt5.QtCore import Qt, QAbstractTableModel, QModelIndex, QVariant, pyqtSignal


@dataclass
class CsvFormat:
    """File conventions retained when an existing document is saved."""
    encoding: str = 'utf-8'
    delimiter: str = ','
    lineterminator: str = '\r\n'
    quotechar: str = '"'
    doublequote: bool = True
    escapechar: str = None
    terminal_newline: bool = True
    bom: bytes = b''


def _decode_bytes(raw):
    # UTF-32 LE begins with the UTF-16 LE BOM, so test UTF-32 first.
    for bom, encoding in (
        (codecs.BOM_UTF32_LE, 'utf-32-le'),
        (codecs.BOM_UTF32_BE, 'utf-32-be'),
        (codecs.BOM_UTF8, 'utf-8-sig'),
        (codecs.BOM_UTF16_LE, 'utf-16-le'),
        (codecs.BOM_UTF16_BE, 'utf-16-be'),
    ):
        if raw.startswith(bom):
            codec = 'utf-8' if encoding == 'utf-8-sig' else encoding
            return raw[len(bom):].decode(codec), encoding, bom
    # Validate the complete file: a long ASCII prefix does not prove UTF-8.
    for encoding in ('utf-8', 'gbk', 'gb18030', 'latin-1'):
        try:
            return raw.decode(encoding), encoding, b''
        except UnicodeDecodeError:
            continue
    raise UnicodeError('Cannot decode CSV file')


def detect_encoding(filepath):
    with open(filepath, 'rb') as file:
        return _decode_bytes(file.read())[1]


_CSV_PARSE_LOCK = threading.RLock()


def _parse_csv_text(text, *, limit_rows=None, **reader_options):
    # The csv module's field limit is process-wide. Serialize our readers and
    # always restore it, including malformed input and failed allocations.
    with _CSV_PARSE_LOCK:
        previous_limit = csv.field_size_limit()
        try:
            csv.field_size_limit(max(previous_limit, len(text)))
            reader = csv.reader(io.StringIO(text, newline=''), **reader_options)
            return list(reader if limit_rows is None else islice(reader, limit_rows))
        finally:
            csv.field_size_limit(previous_limit)


def parse_csv_text(text, *, delimiter=',', quotechar='"', doublequote=True,
                   escapechar=None, strict=True):
    """Parse CSV/clipboard text, including fields larger than csv's default limit."""
    return _parse_csv_text(text, delimiter=delimiter, quotechar=quotechar,
                           doublequote=doublequote, escapechar=escapechar, strict=strict)


def _detect_dialect(text, filepath):
    sample = text[:65536]
    sniffed = None
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
        sniffed = (dialect.delimiter, dialect.quotechar,
                   dialect.doublequote or dialect.escapechar is None, dialect.escapechar)
    except csv.Error:
        pass
    # Sniffer can select punctuation inside ragged quoted cells, even when its
    # guess parses without errors. Compare complete record widths as well.
    preferred = '\t' if os.path.splitext(os.fspath(filepath))[1].lower() == '.tsv' else ','
    best, best_score = (preferred, '"', True, None), (0, 0, 0, 0)
    candidates = [
        (delimiter, '"', True, None) for delimiter in dict.fromkeys((preferred, ',', '\t', ';', '|'))
    ] + ([sniffed] if sniffed else [])
    for candidate in candidates:
        try:
            # A prefix may end before a long first field reaches its separator.
            parsed = _parse_csv_text(text, limit_rows=20, delimiter=candidate[0],
                                     quotechar=candidate[1], doublequote=candidate[2],
                                     escapechar=candidate[3], strict=True)
        except csv.Error:
            continue
        counts = Counter(len(row) for row in parsed if len(row) > 1)
        if counts:
            width, frequency = max(counts.items(), key=lambda item: (item[1], item[0]))
            first_record = next((row for row in parsed if row), [])
            # A wrong dialect may split embedded quoted newlines into many
            # similarly sized physical rows. Prefer separators evidenced by
            # the first nonempty record before counting those repeated widths.
            score = (int(len(first_record) > 1), frequency, width,
                     sum(len(row) - 1 for row in parsed if row))
        else:
            score = (0, 0, 0, 0)
        # Standard double quotes win ties: a literal 'value' in an ordinary
        # CSV must not be stripped just because Sniffer guesses apostrophes.
        if score > best_score:
            best, best_score = candidate, score
    return best


def _detect_line_ending(text, delimiter, quotechar, doublequote, escapechar):
    endings = Counter()
    quoted, field_start, position = False, True, 0
    while position < len(text):
        char = text[position]
        if escapechar and char == escapechar:
            position += 2
            field_start = False
            continue
        if quoted:
            if char == quotechar:
                if doublequote and position + 1 < len(text) and text[position + 1] == quotechar:
                    position += 2
                    continue
                quoted = False
        elif char == quotechar and field_start:
            quoted = True
            field_start = False
        elif char == delimiter:
            field_start = True
        elif char in '\r\n':
            ending = '\r\n' if char == '\r' and text[position:position + 2] == '\r\n' else char
            endings[ending] += 1
            position += len(ending)
            field_start = True
            continue
        else:
            field_start = False
        position += 1
    return endings.most_common(1)[0][0] if endings else '\r\n'


def read_csv_document(filepath):
    """Return unpadded rows and the encoding/dialect needed to save them."""
    with open(filepath, 'rb') as file:
        text, encoding, bom = _decode_bytes(file.read())
    delimiter, quotechar, doublequote, escapechar = _detect_dialect(text, filepath)
    csv_format = CsvFormat(
        encoding=encoding, delimiter=delimiter, quotechar=quotechar,
        doublequote=doublequote, escapechar=escapechar, bom=bom,
        lineterminator=_detect_line_ending(text, delimiter, quotechar, doublequote, escapechar),
        terminal_newline=text.endswith(('\r', '\n')),
    )
    # Keep whitespace after delimiters as cell data, even when Sniffer reports
    # skipinitialspace. Preserve embedded newlines through newline=''.
    return parse_csv_text(text, delimiter=delimiter, quotechar=quotechar,
                          doublequote=doublequote, escapechar=escapechar), csv_format


def read_csv(filepath):
    """Compatibility API returning (rows, encoding)."""
    rows, csv_format = read_csv_document(filepath)
    return rows, csv_format.encoding


def csv_format_for_save(csv_format, write_bom=False):
    """Apply the BOM preference; unsupported encodings never get a header."""
    if not isinstance(csv_format, CsvFormat):
        raise TypeError('csv_format must be a CsvFormat instance')
    if csv_format.lineterminator not in ('\r', '\n', '\r\n'):
        raise ValueError('CSV record ending must be CR, LF or CRLF')
    if csv_format.delimiter in ('\r', '\n'):
        raise ValueError('CSV separator cannot be a record ending')
    # Validate dialect arguments before creating any temporary file.
    csv.writer(io.StringIO(), delimiter=csv_format.delimiter,
               quotechar=csv_format.quotechar, doublequote=csv_format.doublequote,
               escapechar=csv_format.escapechar)
    codec = codecs.lookup(csv_format.encoding).name
    if not write_bom and (codec == 'utf-8-sig' or codec.startswith(('utf-16', 'utf-32'))):
        # UTF-16/32 without a BOM cannot be reliably detected on reopening.
        codec = 'utf-8'
    if write_bom:
        codec = {'utf-8': 'utf-8-sig', 'utf-16': 'utf-16-le', 'utf-32': 'utf-32-le'}.get(codec, codec)
        bom = {
            'utf-8-sig': codecs.BOM_UTF8,
            'utf-16-le': codecs.BOM_UTF16_LE,
            'utf-16-be': codecs.BOM_UTF16_BE,
            'utf-32-le': codecs.BOM_UTF32_LE,
            'utf-32-be': codecs.BOM_UTF32_BE,
        }.get(codec, b'')
    else:
        bom = b''
    return replace(csv_format, encoding=codec, bom=bom)


def write_csv(filepath, rows, encoding='utf-8', *, csv_format=None, write_bom=False):
    """Apply the BOM setting and atomically save, retaining the original on errors."""
    file_format = csv_format_for_save(
        csv_format if csv_format is not None else CsvFormat(encoding=encoding), write_bom=write_bom
    )
    # Write headers explicitly to avoid utf-8-sig adding a second BOM.
    codec = 'utf-8' if file_format.encoding == 'utf-8-sig' else file_format.encoding
    path = os.path.abspath(os.fspath(filepath))
    file_descriptor, temporary_path = tempfile.mkstemp(
        prefix='.' + os.path.basename(path) + '.', suffix='.tmp', dir=os.path.dirname(path))
    try:
        binary_file = os.fdopen(file_descriptor, 'w+b')
        file_descriptor = None  # fdopen now owns the descriptor.
        with binary_file as binary:
            binary.write(file_format.bom)
            with io.TextIOWrapper(binary, encoding=codec, newline='') as stream:
                record_buffer = io.StringIO(newline='')
                # Use both CR and LF for quoting decisions, then replace only
                # the record terminator. A lone CR inside an LF file is data.
                writer = csv.writer(record_buffer, delimiter=file_format.delimiter,
                                    quotechar=file_format.quotechar,
                                    doublequote=file_format.doublequote,
                                    escapechar=file_format.escapechar,
                                    lineterminator='\r\n')
                wrote_row = False
                last_record = ''
                for row in rows:
                    if not wrote_row:
                        row = list(row)
                    record_buffer.seek(0)
                    record_buffer.truncate(0)
                    writer.writerow(row)
                    last_record = record_buffer.getvalue()[:-2]
                    if not wrote_row and not file_format.bom and last_record.startswith('\ufeff'):
                        # A literal U+FEFF in the first cell is data, not a file
                        # header. Quote the record so reopening cannot strip it.
                        record_buffer.seek(0)
                        record_buffer.truncate(0)
                        if file_format.quotechar is None:
                            raise ValueError('A leading U+FEFF cell needs a CSV quote character')
                        csv.writer(record_buffer, delimiter=file_format.delimiter,
                                   quotechar=file_format.quotechar, doublequote=file_format.doublequote,
                                   escapechar=file_format.escapechar, quoting=csv.QUOTE_ALL,
                                   lineterminator='\r\n').writerow(row)
                        last_record = record_buffer.getvalue()[:-2]
                    stream.write(last_record + file_format.lineterminator)
                    wrote_row = True
                stream.flush()
                if wrote_row and last_record and not file_format.terminal_newline:
                    # Preserve a final record without a terminator.
                    binary.truncate(binary.tell() - len(file_format.lineterminator.encode(codec)))
                binary.flush()
                os.fsync(binary.fileno())
        if os.path.exists(path):
            os.chmod(temporary_path, stat.S_IMODE(os.stat(path).st_mode))
        os.replace(temporary_path, path)
    finally:
        primary_error = sys.exc_info()[1]
        if file_descriptor is not None:
            os.close(file_descriptor)
        try:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            except PermissionError:
                # Only make our read-only temporary writable; never the target.
                os.chmod(temporary_path, stat.S_IMODE(os.stat(temporary_path).st_mode) | stat.S_IWRITE)
                os.unlink(temporary_path)
        except OSError as cleanup_error:
            if primary_error is None:
                raise
            if hasattr(primary_error, 'add_note'):
                primary_error.add_note(f'Temporary cleanup failed: {temporary_path}: {cleanup_error}')


class CsvTableModel(QAbstractTableModel):
    """Rectangular editable grid retaining each source record's field count."""
    operationFailed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = []
        self._headers = []
        self._column_count = 0
        self._row_lengths = []
        self._document_generation = 0
        self._operation_failure_serial = 0
        self.operationFailed.connect(self._record_operation_failure)

    def _record_operation_failure(self, _message):
        self._operation_failure_serial += 1

    @staticmethod
    def _valid_number(value):
        # Qt model dimensions and notification ranges use signed 32-bit ints.
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2147483647

    def _valid_index(self, index):
        return (isinstance(index, QModelIndex) and index.isValid() and index.model() is self
                and 0 <= index.row() < len(self._data)
                and 0 <= index.column() < self._column_count)

    def rowCount(self, parent=QModelIndex()):
        return 0 if not isinstance(parent, QModelIndex) or parent.isValid() else len(self._data)

    def columnCount(self, parent=QModelIndex()):
        return 0 if not isinstance(parent, QModelIndex) or parent.isValid() else self._column_count

    def data(self, index, role=Qt.DisplayRole):
        if self._valid_index(index) and role in (Qt.DisplayRole, Qt.EditRole, Qt.ToolTipRole):
            row, col = index.row(), index.column()
            if 0 <= row < self.rowCount() and 0 <= col < self.columnCount():
                value = self._data[row][col]
                return value if role != Qt.ToolTipRole or len(value) <= 2000 else value[:2000] + '…'
        return QVariant()

    def setData(self, index, value, role=Qt.EditRole):
        if not self._valid_index(index) or role != Qt.EditRole:
            return False
        return self._set_data(index.row(), index.column(), value)

    def _set_data(self, row, col, value):
        if not (self._valid_number(row) and self._valid_number(col)
                and row < self.rowCount() and col < self.columnCount()):
            return False
        try:
            value = str(value)
        except Exception:
            return False
        self._data[row][col] = value
        if value:
            self._row_lengths[row] = max(self._row_lengths[row], col + 1)
        index = self.index(row, col)
        self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
        return True

    def flags(self, index):
        return (Qt.ItemIsSelectable | Qt.ItemIsEditable | Qt.ItemIsEnabled) if self._valid_index(index) else Qt.NoItemFlags

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole or not self._valid_number(section):
            return QVariant()
        if orientation == Qt.Horizontal:
            return self._headers[section] if section < len(self._headers) else QVariant()
        if orientation == Qt.Vertical and section < self.rowCount():
            return str(section + 1)
        return QVariant()

    def load_data(self, rows, column_count=None):
        if column_count is not None and not self._valid_number(column_count):
            raise ValueError('column_count must be a nonnegative Qt-sized integer')
        if isinstance(rows, (str, bytes)):
            raise TypeError('rows must be a sequence of records')
        rows = list(rows)
        if not self._valid_number(len(rows)):
            raise ValueError('Too many rows for a Qt table')
        if any(isinstance(row, (str, bytes)) for row in rows):
            raise TypeError('each record must be a sequence of fields')
        copied = [[str(value) for value in row] for row in rows]
        width = max((len(row) for row in copied), default=0)
        if column_count is not None:
            width = max(width, column_count)
        if not self._valid_number(width):
            raise ValueError('Too many columns for a Qt table')
        # Conversion and allocation must finish before beginResetModel. A bad
        # value or allocation error otherwise leaves views inside a reset.
        lengths = [len(row) for row in copied]
        data = [row + [''] * (width - len(row)) for row in copied]
        headers = [self._col_letter(col) for col in range(width)]
        self.beginResetModel()
        self._column_count = width
        self._row_lengths, self._data, self._headers = lengths, data, headers
        self._document_generation += 1
        self.endResetModel()

    def get_all_data(self):
        return [row[:length] for row, length in zip(self._data, self._row_lengths)]

    def get_row_lengths(self):
        return list(self._row_lengths)

    def _restore_row_lengths(self, lengths):
        """Restore field counts after an undo operation (no displayed values change)."""
        if not isinstance(lengths, dict) or any(
                not self._valid_number(row) or not self._valid_number(length)
                for row, length in lengths.items()):
            return False
        for row, length in lengths.items():
            if 0 <= row < self.rowCount():
                self._row_lengths[row] = min(max(0, length), self.columnCount())
        return True

    def _apply_cells(self, cells):
        """Validate and allocate a batch before making any cell changes."""
        try:
            normalized = []
            changed_rows = {}
            lengths = {}
            for row, col, value in cells:
                if not (self._valid_number(row) and self._valid_number(col)
                        and row < self.rowCount() and col < self.columnCount()):
                    return False
                value = str(value)
                normalized.append((row, col, value))
                if row not in changed_rows:
                    changed_rows[row] = self._data[row][:]
                    lengths[row] = self._row_lengths[row]
                changed_rows[row][col] = value
                if value:
                    lengths[row] = max(lengths[row], col + 1)
        except Exception:
            return False
        for row, values in changed_rows.items():
            self._data[row] = values
            self._row_lengths[row] = lengths[row]
        for row, col, _ in normalized:
            index = self.index(row, col)
            self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
        return True

    def _resize_and_apply(self, rows, cols, cells, restored_lengths=None):
        """Prepare an entire paste/undo before notifying views of grid growth."""
        if not (self._valid_number(rows) and self._valid_number(cols)):
            return False
        old_rows, old_cols = self.rowCount(), self.columnCount()
        try:
            data, lengths = [], []
            for row in range(rows):
                if row < old_rows:
                    data.append(self._data[row][:cols] + [''] * max(0, cols - old_cols))
                    length = self._row_lengths[row]
                    lengths.append(cols if length == old_cols else min(length, cols))
                else:
                    data.append([''] * cols)
                    lengths.append(cols)
            final_data, final_lengths = list(data), list(lengths)
            edited_rows = set()
            normalized = []
            for row, col, value in cells:
                if not (self._valid_number(row) and self._valid_number(col) and row < rows and col < cols):
                    return False
                value = str(value)
                if row not in edited_rows:
                    final_data[row] = data[row][:]
                    edited_rows.add(row)
                final_data[row][col] = value
                if value:
                    final_lengths[row] = max(final_lengths[row], col + 1)
                normalized.append((row, col))
            if restored_lengths is not None:
                for row, length in restored_lengths.items():
                    if not (self._valid_number(row) and self._valid_number(length)):
                        return False
                    if row < rows:
                        final_lengths[row] = min(length, cols)
            headers = [self._col_letter(col) for col in range(cols)]
            retained_count = min(rows, old_rows)
            retained_data = data[:retained_count]
            retained_lengths = lengths[:retained_count]
            removed_data = self._data[:rows] if rows < old_rows else None
            removed_lengths = self._row_lengths[:rows] if rows < old_rows else None
        except Exception:
            return False
        parent = QModelIndex()
        if rows < old_rows:
            self.beginRemoveRows(parent, rows, old_rows - 1)
            self._data, self._row_lengths = removed_data, removed_lengths
            self.endRemoveRows()
        if cols != old_cols:
            if cols > old_cols:
                self.beginInsertColumns(parent, old_cols, cols - 1)
            else:
                self.beginRemoveColumns(parent, cols, old_cols - 1)
            self._data, self._row_lengths = retained_data, retained_lengths
            self._column_count, self._headers = cols, headers
            if cols > old_cols:
                self.endInsertColumns()
            else:
                self.endRemoveColumns()
        if rows > old_rows:
            self.beginInsertRows(parent, old_rows, rows - 1)
            self._data, self._row_lengths = data, lengths
            self.endInsertRows()
        # Existing cell values must stay stable during insert/remove signals;
        # proxies and Qt's model tester inspect neighboring cells there.
        self._data, self._row_lengths = final_data, final_lengths
        for row, col in normalized:
            index = self.index(row, col)
            self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
        return True

    def get_block(self, start_row, start_col, rows, cols):
        if not all(self._valid_number(value) for value in (start_row, start_col, rows, cols)):
            return []
        return [[self._data[row][col] if 0 <= row < self.rowCount() and 0 <= col < self.columnCount() else ''
                 for col in range(start_col, start_col + cols)]
                for row in range(start_row, start_row + rows)]

    def insertRows(self, row, count, parent=QModelIndex()):
        if (not isinstance(parent, QModelIndex) or parent.isValid()
                or not self._valid_number(row) or not self._valid_number(count)
                or count == 0 or row > self.rowCount()
                or not self._valid_number(self.rowCount() + count)):
            return False
        try:
            data = self._data[:row] + [[''] * self.columnCount() for _ in range(count)] + self._data[row:]
            lengths = self._row_lengths[:row] + [self.columnCount()] * count + self._row_lengths[row:]
        except (MemoryError, OverflowError):
            return False
        self.beginInsertRows(parent, row, row + count - 1)
        self._data, self._row_lengths = data, lengths
        self.endInsertRows()
        return True

    def removeRows(self, row, count, parent=QModelIndex()):
        if (not isinstance(parent, QModelIndex) or parent.isValid()
                or not self._valid_number(row) or not self._valid_number(count)
                or count == 0 or row + count > self.rowCount()):
            return False
        self.beginRemoveRows(parent, row, row + count - 1)
        del self._data[row:row + count]
        del self._row_lengths[row:row + count]
        self.endRemoveRows()
        return True

    def insertColumns(self, col, count, parent=QModelIndex()):
        if (not isinstance(parent, QModelIndex) or parent.isValid()
                or not self._valid_number(col) or not self._valid_number(count)
                or count == 0 or col > self.columnCount()
                or not self._valid_number(self.columnCount() + count)):
            return False
        try:
            data = [row[:col] + [''] * count + row[col:] for row in self._data]
            lengths = [length + count if col <= length else length for length in self._row_lengths]
            width = self._column_count + count
            headers = [self._col_letter(index) for index in range(width)]
        except (MemoryError, OverflowError):
            return False
        self.beginInsertColumns(parent, col, col + count - 1)
        self._data, self._row_lengths, self._headers = data, lengths, headers
        self._column_count = width
        self.endInsertColumns()
        return True

    def removeColumns(self, col, count, parent=QModelIndex()):
        if (not isinstance(parent, QModelIndex) or parent.isValid()
                or not self._valid_number(col) or not self._valid_number(count)
                or count == 0 or col + count > self.columnCount()):
            return False
        try:
            data = [row[:col] + row[col + count:] for row in self._data]
            lengths = [length - min(count, max(0, length - col)) for length in self._row_lengths]
            width = self._column_count - count
            headers = [self._col_letter(index) for index in range(width)]
        except (MemoryError, OverflowError):
            return False
        self.beginRemoveColumns(parent, col, col + count - 1)
        self._data, self._row_lengths, self._headers = data, lengths, headers
        self._column_count = width
        self.endRemoveColumns()
        return True

    def _update_headers(self):
        self._headers = [self._col_letter(col) for col in range(self.columnCount())]

    @staticmethod
    def _col_letter(col):
        result = ''
        col += 1
        while col > 0:
            col -= 1
            result = chr(ord('A') + col % 26) + result
            col //= 26
        return result
