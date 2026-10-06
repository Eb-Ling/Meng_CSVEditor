# -*- coding: utf-8 -*-
"""Shared visual theme and font-independent vector icons for Meng CSV Editor."""

from PyQt5.QtCore import Qt, QPointF, QRectF
from PyQt5.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap


BACKGROUND = '#f4f6fa'
SURFACE = '#ffffff'
TEXT = '#17243b'
MUTED = '#75839a'
GRID = '#e5ebf3'
ACCENT = '#3478f6'
SELECTION = '#e6efff'
ALTERNATE_ROW = '#fafbfd'


def _draw_icon(painter, name):
    """Draw a small line icon in a 24 x 24 logical coordinate system."""
    line = lambda x1, y1, x2, y2: painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    rectangle = lambda x, y, w, h: painter.drawRect(QRectF(x, y, w, h))
    rounded = lambda x, y, w, h, r=2: painter.drawRoundedRect(QRectF(x, y, w, h), r, r)

    def path(points):
        drawing = QPainterPath(QPointF(*points[0]))
        for point in points[1:]:
            drawing.lineTo(QPointF(*point))
        painter.drawPath(drawing)

    if name == 'app':
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(ACCENT))
        rounded(1, 1, 22, 22, 6)
        painter.setPen(QPen(QColor(SURFACE), 1.45, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        rounded(5, 5, 14, 14, 1.5)
        line(5, 10, 19, 10)
        line(5, 14.5, 19, 14.5)
        line(10, 5, 10, 19)
    elif name == 'new':
        path([(14, 3), (6, 3), (6, 21), (19, 21), (19, 8), (14, 3), (14, 8), (19, 8)])
        line(9, 14, 16, 14)
        line(12.5, 10.5, 12.5, 17.5)
    elif name == 'open':
        path([(3, 18), (3, 6), (9, 6), (11, 8), (20, 8), (20, 10)])
        path([(3, 20), (7, 11), (22, 11), (18, 20), (3, 20)])
    elif name in ('save', 'save_as'):
        path([(5, 3), (17, 3), (21, 7), (21, 21), (3, 21), (3, 3), (5, 3)])
        rectangle(7, 3, 9, 6)
        rounded(7, 13, 10, 8, 1)
        if name == 'save_as':
            painter.save()
            painter.setBrush(QColor(SURFACE if painter.pen().color().name() != '#ffffff' else ACCENT))
            painter.drawEllipse(QRectF(13, 12, 11, 11))
            painter.restore()
            path([(16, 19), (20, 15), (21, 16), (17, 20), (16, 20), (16, 19)])
    elif name in ('undo', 'redo'):
        if name == 'redo':
            painter.translate(24, 0)
            painter.scale(-1, 1)
        path([(8, 5), (3, 10), (8, 15)])
        drawing = QPainterPath(QPointF(3, 10))
        drawing.lineTo(13, 10)
        drawing.cubicTo(20, 10, 22, 17, 18, 21)
        painter.drawPath(drawing)
    elif name in ('find', 'replace'):
        painter.drawEllipse(QRectF(3, 3, 12, 12))
        line(13.5, 13.5, 21, 21)
        if name == 'replace':
            line(6, 9, 12, 9)
            path([(10, 6.5), (12.5, 9), (10, 11.5)])
    elif name == 'settings':
        line(4, 6, 20, 6)
        line(4, 12, 20, 12)
        line(4, 18, 20, 18)
        painter.setBrush(painter.pen().color())
        rounded(7, 3.5, 3, 5, 1)
        rounded(14, 9.5, 3, 5, 1)
        rounded(8, 15.5, 3, 5, 1)
    elif name in ('grid', 'table'):
        rounded(3, 3, 18, 18, 3)
        line(3, 9, 21, 9)
        line(3, 15, 21, 15)
        line(9, 3, 9, 21)
        if name == 'grid':
            line(15, 3, 15, 21)
    elif name == 'chevron':
        path([(8, 5), (15, 12), (8, 19)])
    elif name == 'delete':
        line(3, 6, 21, 6)
        path([(9, 6), (9, 3), (15, 3), (15, 6)])
        path([(6, 6), (7, 21), (17, 21), (18, 6)])
        line(10, 10, 10, 17)
        line(14, 10, 14, 17)
    elif name in ('row', 'column'):
        rounded(3, 3, 18, 18, 2)
        if name == 'row':
            rectangle(3, 9, 18, 6)
            line(9, 3, 9, 21)
            line(15, 3, 15, 21)
        else:
            rectangle(9, 3, 6, 18)
            line(3, 9, 21, 9)
            line(3, 15, 21, 15)
    else:
        rounded(4, 4, 16, 16, 3)


def make_icon(name, color=None, size=24):
    """Return a crisp line-art QIcon, including an explicit muted disabled state."""
    icon = QIcon()
    normal = QColor(color or TEXT)
    for pixels in sorted({16, 18, 20, 24, 32, 48, 64, int(size)}):
        for mode, ink in ((QIcon.Normal, normal), (QIcon.Disabled, QColor('#b5c0ce'))):
            pixmap = QPixmap(pixels, pixels)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.scale(pixels / 24.0, pixels / 24.0)
            pen = QPen(ink, 1.65, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            _draw_icon(painter, name)
            painter.end()
            icon.addPixmap(pixmap, mode)
    return icon


STYLESHEET = '''
QMainWindow, QDialog { background: #f4f6fa; color: #17243b; }
QWidget { color: #17243b; }
QWidget#workspace { background: #f4f6fa; }
QFrame#documentHeader { background: transparent; border: none; }
QFrame#tableCard { background: #ffffff; border: 1px solid #e5ebf3; border-radius: 10px; }
QLabel { background: transparent; border: none; }
QLabel#documentTitle { font-size: 20px; font-weight: 600; color: #17243b; }
QLabel#documentPath { color: #75839a; font-size: 11px; }
QLabel#documentMeta { color: #75839a; font-size: 11px; }
QLabel#documentMeta { background: #edf2f8; padding: 5px 10px; border-radius: 10px; }
QLabel#documentMeta[dirty="true"] { color: #a76b15; background: #fff1d9; }
QLabel#documentIcon { background: #e6efff; border-radius: 10px; }
QLabel#toolbarHint { color: #8a97aa; font-size: 11px; padding-right: 12px; }
QLabel#brandLabel { color: #3478f6; font-size: 12px; font-weight: 600; padding-right: 12px; }
QLabel#statusPill { color: #3478f6; background: #e6efff; padding: 4px 10px; border-radius: 10px; font-size: 11px; }
QMenuBar { background: #ffffff; border-bottom: 1px solid #e5ebf3; spacing: 3px; padding: 4px 10px; }
QMenuBar::item { background: transparent; padding: 5px 9px; border-radius: 5px; }
QMenuBar::item:selected { background: #edf3ff; color: #2564d8; }
QMenuBar::item:pressed { background: #e6efff; }
QMenu { background: #ffffff; border: 1px solid #dce4ee; padding: 6px; }
QMenu::item { padding: 7px 32px 7px 26px; border-radius: 4px; }
QMenu::item:selected { background: #edf3ff; color: #2564d8; }
QMenu::item:disabled { color: #aeb8c8; }
QMenu::separator { height: 1px; background: #e5ebf3; margin: 5px 8px; }
QToolBar { background: #ffffff; border: none; border-bottom: 1px solid #e5ebf3; spacing: 4px; padding: 8px 12px; }
QToolBar::separator { background: #e5ebf3; width: 1px; margin: 5px 9px; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 5px 9px; min-height: 20px; }
QToolButton:hover { background: #edf3ff; border-color: #dce8ff; }
QToolButton:pressed, QToolButton:checked { background: #e6efff; border-color: #cfdef8; }
QToolButton:disabled { color: #aeb8c8; background: transparent; border-color: transparent; }
QToolButton#primaryAction { background: #3478f6; color: #ffffff; border: 1px solid #3478f6; padding: 5px 14px; font-weight: 600; }
QToolButton#primaryAction:hover { background: #2468e7; border-color: #2468e7; }
QToolButton#primaryAction:pressed { background: #1d5ecf; border-color: #1d5ecf; }
QToolButton#primaryAction:disabled { background: #dfe7f2; border-color: #dfe7f2; color: #97a5b8; }
QTableView { background: #ffffff; alternate-background-color: #fafbfd; color: #17243b; border: none; gridline-color: #e5ebf3; selection-background-color: #e6efff; selection-color: #17243b; outline: none; }
QTableView::item { padding: 5px 9px; border: none; }
QTableView::item:selected { background: #e6efff; color: #17243b; }
QTableView::item:focus { border: 1px solid #3478f6; }
QHeaderView { background: #f5f7fb; }
QHeaderView::section { background: #f5f7fb; color: #5c6c84; padding: 7px 9px; border: none; border-right: 1px solid #e5ebf3; border-bottom: 1px solid #e5ebf3; font-weight: 600; }
QHeaderView::section:checked { background: #e6efff; color: #2564d8; }
QHeaderView::section:hover { background: #edf3ff; }
QTableCornerButton::section { background: #f5f7fb; border: none; border-right: 1px solid #e5ebf3; border-bottom: 1px solid #e5ebf3; }
QStatusBar { background: #ffffff; color: #75839a; border-top: 1px solid #e5ebf3; padding: 3px 10px; }
QStatusBar::item { border: none; }
QStatusBar QLabel { color: #75839a; padding: 2px 5px; }
QStatusBar QLabel#statusPill { color: #3478f6; }
QLineEdit, QPlainTextEdit, QTextEdit { background: #ffffff; color: #17243b; border: 1px solid #d6dfeb; border-radius: 5px; padding: 6px 8px; selection-background-color: #e6efff; selection-color: #17243b; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus { border-color: #3478f6; }
QLineEdit:disabled { color: #a0adbf; background: #f5f7fb; }
QPushButton { background: #ffffff; color: #31425b; border: 1px solid #d6dfeb; border-radius: 6px; padding: 7px 16px; min-width: 60px; }
QPushButton:hover { border-color: #b9cef3; background: #edf3ff; color: #2564d8; }
QPushButton:pressed { background: #e6efff; }
QPushButton:default { background: #3478f6; color: #ffffff; border-color: #3478f6; }
QPushButton:default:hover { background: #2468e7; border-color: #2468e7; }
QPushButton:default:pressed { background: #1d5ecf; }
QPushButton:disabled { background: #f5f7fb; color: #aeb8c8; border-color: #e5ebf3; }
QCheckBox { spacing: 8px; padding: 3px 0; }
QCheckBox::indicator { width: 16px; height: 16px; }
QGroupBox { background: #ffffff; border: 1px solid #e5ebf3; border-radius: 7px; margin-top: 12px; padding-top: 10px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 5px; left: 10px; }
QComboBox { background: #ffffff; border: 1px solid #d6dfeb; border-radius: 5px; padding: 6px 10px; }
QComboBox:hover, QComboBox:focus { border-color: #3478f6; }
QComboBox QAbstractItemView { background: #ffffff; selection-background-color: #e6efff; selection-color: #17243b; border: 1px solid #d6dfeb; }
QScrollBar:vertical { background: #f5f7fb; width: 11px; margin: 0; border: none; }
QScrollBar::handle:vertical { background: #c6d0de; border-radius: 4px; min-height: 28px; margin: 2px; }
QScrollBar::handle:vertical:hover { background: #a9b8cc; }
QScrollBar:horizontal { background: #f5f7fb; height: 11px; margin: 0; border: none; }
QScrollBar::handle:horizontal { background: #c6d0de; border-radius: 4px; min-width: 28px; margin: 2px; }
QScrollBar::handle:horizontal:hover { background: #a9b8cc; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QToolTip { background: #17243b; color: #ffffff; border: 1px solid #17243b; padding: 5px 8px; }
'''


def apply_theme(app):
    """Install a consistent light palette, Windows font fallback, and widget skin."""
    app.setStyle('Fusion')
    installed = set(QFontDatabase().families())
    family = next((name for name in ('Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI', 'Noto Sans CJK SC', 'Arial') if name in installed), app.font().family())
    font = QFont(family, 9)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)
    palette = QPalette()
    for role, color in (
        (QPalette.Window, BACKGROUND), (QPalette.WindowText, TEXT),
        (QPalette.Base, SURFACE), (QPalette.AlternateBase, ALTERNATE_ROW),
        (QPalette.ToolTipBase, TEXT), (QPalette.ToolTipText, SURFACE),
        (QPalette.Text, TEXT), (QPalette.Button, SURFACE),
        (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT),
        (QPalette.HighlightedText, SURFACE), (QPalette.Link, ACCENT),
    ):
        palette.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor('#aeb8c8'))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)
    app.setWindowIcon(make_icon('app'))
