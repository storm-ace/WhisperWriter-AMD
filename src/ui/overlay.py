"""
Floating recorder pill shown while dictating.

Never takes keyboard focus, so the text still lands in the window you were typing in.
States: recording (timer + live waveform), transcribing, typed (preview, fades out) and
message (errors / hints).
"""

import time

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QVBoxLayout,
                               QWidget)

from ui import theme
from ui.widgets import Waveform


class _Pill(QWidget):
    """Rounded translucent surface that hosts the overlay content."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.radius = 28

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                            self.radius, self.radius)
        fill = QColor(theme.SURFACE)
        fill.setAlpha(244)
        p.fillPath(path, fill)
        p.setPen(QPen(QColor(theme.BORDER_2), 1))
        p.drawPath(path)


class RecorderOverlay(QWidget):
    stopRequested = Signal()
    cancelRequested = Signal()

    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 22)
        self.pill = _Pill()
        shadow_color = QColor(theme.SHADOW)
        shadow_color.setAlpha(150 if theme.NAME == 'dark' else 70)
        shadow = QGraphicsDropShadowEffect(self.pill, blurRadius=28, offset=QPoint(0, 8),
                                           color=shadow_color)
        self.pill.setGraphicsEffect(shadow)
        outer.addWidget(self.pill)

        # Plain layout with only the current page visible: unlike QStackedLayout its size
        # hint follows the visible page, so the pill shrinks and grows per state.
        pill_lay = QVBoxLayout(self.pill)
        pill_lay.setContentsMargins(0, 0, 0, 0)
        self.pages = [self._build_recording(), self._build_busy(), self._build_result()]
        for page in self.pages:
            pill_lay.addWidget(page)

        self._started = 0.0
        self.tick = QTimer(self, interval=250, timeout=self._update_timer)
        self.hide_timer = QTimer(self, singleShot=True, timeout=self._on_card_timeout)
        self._return_to_recording = False

    # ------------------------------------------------------------------ build
    def _row(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 10, 10, 10)
        lay.setSpacing(12)
        return w, lay

    def _build_recording(self):
        w, lay = self._row()
        lay.addWidget(theme.dot(theme.ACCENT, 10))
        self.timer_label = theme.label('0:00', 'value')
        self.timer_label.setFixedWidth(38)
        lay.addWidget(self.timer_label)
        self.wave = Waveform(bars=20)
        self.wave.active = True
        lay.addWidget(self.wave)
        mic_icon = QLabel()
        mic_icon.setPixmap(theme.icon('mic', theme.FAINT).pixmap(13, 13))
        lay.addWidget(mic_icon)
        lay.addSpacing(-8)
        self.mic_label = theme.label('', 'faint')
        self.mic_label.setToolTip('Microphone in use')
        lay.addWidget(self.mic_label)
        self.target_label = theme.label('', 'faint')
        lay.addWidget(self.target_label)
        stop = theme.tool_button('stop', 'Stop and transcribe', theme.TEXT, 14)
        stop.setProperty('role', 'pill-stop')
        stop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        stop.clicked.connect(self.stopRequested)
        lay.addWidget(stop)
        cancel = theme.tool_button('x', 'Cancel (discard recording)', theme.MUTED, 14)
        cancel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        cancel.clicked.connect(self.cancelRequested)
        lay.addWidget(cancel)
        return w

    def _build_busy(self):
        w, lay = self._row()
        lay.setContentsMargins(18, 14, 20, 14)
        self.spinner = _Spinner()
        lay.addWidget(self.spinner)
        self.busy_label = QLabel('Transcribing…')
        lay.addWidget(self.busy_label)
        self.busy_detail = theme.label('', 'info-mono')
        lay.addWidget(self.busy_detail)
        return w

    def _build_result(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.result_icon = QLabel()
        head.addWidget(self.result_icon)
        self.result_head = theme.label('', 'faint')
        head.addWidget(self.result_head, 1)
        self.result_hint = theme.label('', 'mono')
        head.addWidget(self.result_hint)
        lay.addLayout(head)
        self.result_text = theme.label('', 'body', wrap=True)
        lay.addWidget(self.result_text)
        return w

    # ------------------------------------------------------------------ states
    @property
    def is_showing_result(self):
        return self.isVisible() and self.hide_timer.isActive()

    def show_recording(self, target_label='', mic=None, restart_timer=True):
        self.hide_timer.stop()
        self._return_to_recording = False
        self.pill.radius = 28
        if restart_timer or not self._started:
            self._started = time.monotonic()
        self.wave.reset()
        if mic is not None:
            self.mic_label.setText(f'{mic}')
        self.target_label.setText(f'→ {target_label}' if target_label else '')
        self.target_label.setVisible(bool(target_label))
        self._update_timer()
        self.tick.start()
        self._show_page(0)
        self._present()

    def push_level(self, level):
        self.wave.push(level)

    def show_transcribing(self, detail=''):
        self.tick.stop()
        self.hide_timer.stop()
        self.pill.radius = 28
        seconds = time.monotonic() - self._started if self._started else 0
        self.busy_label.setText(f'Transcribing {self._fmt(seconds)} of audio' if seconds else
                                'Transcribing…')
        self.busy_detail.setText(detail)
        self.busy_detail.setVisible(bool(detail))
        self.spinner.start()
        self._show_page(1)
        self._present()

    def show_result(self, text, head, hint='', ms=2600, then_recording=False):
        """Show what was typed. With `then_recording` (continuous mode) the pill returns to
        the listening view afterwards instead of hiding."""
        self._show_card('check', theme.INFO, text, head, hint, ms)
        self._return_to_recording = then_recording

    def show_message(self, text, head='WhisperWriter', error=True, ms=4000):
        self._show_card('x' if error else 'check', theme.DANGER if error else theme.INFO,
                        text, head, '', ms)

    def _show_card(self, icon_name, color, text, head, hint, ms):
        self._return_to_recording = False
        self.tick.stop()
        self.spinner.stop()
        self.pill.radius = 16
        self.result_icon.setPixmap(theme.icon(icon_name, color).pixmap(14, 14))
        self.result_head.setText(head)
        self.result_hint.setText(hint)
        shown = text.strip()
        shown = shown if len(shown) < 220 else shown[:217] + '…'
        self.result_text.setText(shown)
        # Fixed width so the wrapped label reports its real height to adjustSize().
        self.result_text.ensurePolished()   # pick up the stylesheet font before measuring
        fm = self.result_text.fontMetrics()
        self.result_text.setFixedWidth(min(480, max(280, fm.horizontalAdvance(shown) + 16)))
        self.result_text.setVisible(bool(shown))
        self._show_page(2)
        self._present()
        self.hide_timer.start(ms)

    def _on_card_timeout(self):
        if self._return_to_recording:
            self.show_recording(self.target_label.text().removeprefix('→ '), restart_timer=False)
        else:
            self.hide()

    def hide(self):
        self._return_to_recording = False
        self._started = 0.0
        self.tick.stop()
        self.spinner.stop()
        super().hide()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _fmt(seconds):
        seconds = int(seconds)
        return f'{seconds // 60}:{seconds % 60:02d}'

    def _update_timer(self):
        self.timer_label.setText(self._fmt(time.monotonic() - self._started))

    def _show_page(self, index):
        for i, page in enumerate(self.pages):
            page.setVisible(i == index)
        self._page = self.pages[index]

    def _present(self):
        # Recompute the size for the new state (shrinks as well as grows).
        for lay in (self._page.layout(), self.pill.layout(), self.layout()):
            lay.invalidate()
            lay.activate()
        self.setFixedSize(self.layout().sizeHint())
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2, area.bottom() - self.height() - 36)
        if not self.isVisible():
            self.show()
        self.raise_()


class _Spinner(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(18, 18)
        self.angle = 0
        self.timer = QTimer(self, interval=16, timeout=self._step)

    def start(self):
        self.timer.start()

    def stop(self):
        self.timer.stop()

    def _step(self):
        self.angle = (self.angle + 8) % 360
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(theme.INFO), 2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(QRectF(2, 2, 14, 14), -self.angle * 16, 270 * 16)
