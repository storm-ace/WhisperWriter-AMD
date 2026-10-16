"""Reusable custom widgets: toggle switch, live waveform, wrapping chip row, toast."""

from collections import deque

from PySide6.QtCore import (Property, QEasingCurve, QPoint, QPropertyAnimation, QRect, QSize,
                            Qt, QTimer)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QLayout, QSizePolicy, QWidget

from ui import theme


class Toggle(QAbstractButton):
    """iOS-style switch. Behaves like a checkable button (toggled/isChecked/setChecked)."""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(36, 20)
        self._offset = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b'offset', self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, checked):
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def get_offset(self):
        return self._offset

    def set_offset(self, value):
        self._offset = value
        self.update()

    offset = Property(float, get_offset, set_offset)

    def sizeHint(self):
        return QSize(36, 20)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off, on = QColor('#34363C'), QColor(theme.ACCENT)
        animating = self._anim.state() == QPropertyAnimation.State.Running
        t = self._offset if animating else (1.0 if self.isChecked() else 0.0)
        track = QColor(int(off.red() + (on.red() - off.red()) * t),
                       int(off.green() + (on.green() - off.green()) * t),
                       int(off.blue() + (on.blue() - off.blue()) * t))
        if not self.isEnabled():
            track.setAlpha(110)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(self.rect(), 10, 10)
        knob = 14
        x = 3 + (self.width() - knob - 6) * t
        p.setBrush(QColor('#FFFFFF'))
        p.drawEllipse(int(x), 3, knob, knob)
        if self.hasFocus():
            p.setPen(QColor(theme.INFO))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)


class Waveform(QWidget):
    """Scrolling bar meter fed with 0..1 input levels."""

    def __init__(self, bars=26, color=theme.ACCENT, idle_color='#3A3C42', parent=None):
        super().__init__(parent)
        self.levels = deque([0.0] * bars, maxlen=bars)
        self.color = QColor(color)
        self.idle_color = QColor(idle_color)
        self.active = False
        self.setMinimumHeight(28)
        self.setFixedWidth(bars * 6)

    def push(self, level):
        self.levels.append(max(0.0, min(1.0, level)))
        self.update()

    def reset(self):
        for i in range(len(self.levels)):
            self.levels[i] = 0.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        h = self.height()
        for i, level in enumerate(self.levels):
            bar_h = max(4, int(4 + level * (h - 4)))
            p.setBrush(self.color if self.active and level > 0.02 else self.idle_color)
            p.drawRoundedRect(i * 6, (h - bar_h) // 2, 3, bar_h, 1.5, 1.5)


class FlowLayout(QLayout):
    """Left-to-right layout that wraps to the next line (for chip rows)."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._layout(QRect(0, 0, width, 0), dry=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, dry=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def clear(self):
        while self._items:
            item = self._items.pop()
            if item.widget():
                item.widget().deleteLater()

    def _layout(self, rect, dry):
        x, y, line_h = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line_h > 0:
                x = rect.x()
                y += line_h + self._spacing
                line_h = 0
            if not dry:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class ChipRow(QWidget):
    def __init__(self, chips=(), role='chip', parent=None):
        super().__init__(parent)
        self.flow = FlowLayout(self)
        self.role = role
        self.set_chips(chips)

    def set_chips(self, chips, role=None):
        self.flow.clear()
        for text in chips:
            chip = QLabel(text)
            chip.setProperty('role', role or self.role)
            self.flow.addWidget(chip)
        self.updateGeometry()


class Toast(QWidget):
    """Small transient message anchored to the bottom-right of its parent."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName('Toast')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self.text = QLabel()
        lay.addWidget(self.text)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.timer = QTimer(self, singleShot=True, timeout=self.hide)
        self.hide()

    def show_message(self, message, ms=2400):
        self.text.setText(message)
        self.adjustSize()
        parent = self.parentWidget()
        self.move(parent.width() - self.width() - 24, parent.height() - self.height() - 24)
        self.raise_()
        self.show()
        self.timer.start(ms)
