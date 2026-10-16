"""Row widgets for the session list and the dictation timeline."""

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QWidget

from ui import theme
from ui.formatting import fmt_clock
from ui.widgets import hbox, vbox


class SessionItem(QWidget):
    """One row in the sidebar: title, optional search snippet, meta line."""

    WIDTH = 212

    def __init__(self, title, meta, active=False, snippet=None):
        super().__init__()
        lay = vbox((12, 9, 12, 9), 3, self)
        top = hbox(spacing=8)
        if active:
            top.addWidget(theme.dot(theme.ACCENT, 6))
        t = theme.label(role='strong')
        t.setTextFormat(Qt.TextFormat.PlainText)
        t.setText(t.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight,
                                             self.WIDTH - (12 if active else 0)))
        t.setToolTip(title)
        top.addWidget(t, 1)
        lay.addLayout(top)
        if snippet:
            lay.addWidget(theme.label(snippet, 'faint', wrap=True))
        m = theme.label('', 'faint')
        m.setText(m.fontMetrics().elidedText(meta, Qt.TextElideMode.ElideRight, self.WIDTH))
        lay.addWidget(m)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


class EntryWidget(QFrame):
    """One dictation in the timeline."""

    copyRequested = Signal(str)
    sendRequested = Signal(str)
    deleteRequested = Signal(int)

    def __init__(self, d):
        super().__init__()
        self.setObjectName('Entry')
        self.dictation_id = d['id']
        lay = hbox((0, 16, 0, 16), 16, self)

        stamp = time.strftime('%H:%M', time.localtime(d['created_at']))
        left = theme.label(f"{stamp}\n{fmt_clock(d['duration_s'])}" if d.get('duration_s') else stamp,
                           'mono')
        left.setFixedWidth(56)
        left.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addWidget(left)

        right = vbox(spacing=8)
        head = hbox(spacing=6)
        if d.get('target_app'):
            head.addWidget(theme.label(f"→ {d['target_app']}", 'chip'))
        if d.get('latency_s'):
            head.addWidget(theme.label(f"{d['latency_s']:.1f} s", 'chip'))
        if d.get('corrected'):
            head.addWidget(theme.label('LLM corrected', 'chip-info'))
        head.addStretch(1)
        send_btn = theme.tool_button('send', 'Send this dictation to Claude', theme.FAINT, 14)
        send_btn.clicked.connect(lambda: self.sendRequested.emit(d['text']))
        head.addWidget(send_btn)
        copy_btn = theme.tool_button('copy', 'Copy text', theme.FAINT, 14)
        copy_btn.clicked.connect(lambda: self.copyRequested.emit(d['text']))
        head.addWidget(copy_btn)
        del_btn = theme.tool_button('trash', 'Delete dictation', theme.FAINT, 14)
        del_btn.clicked.connect(lambda: self.deleteRequested.emit(self.dictation_id))
        head.addWidget(del_btn)
        right.addLayout(head)

        text = theme.label(d['text'], 'body', wrap=True, selectable=True)
        text.setTextFormat(Qt.TextFormat.PlainText)
        right.addWidget(text)
        lay.addLayout(right, 1)


class SummaryBox(QWidget):
    """Collapsible LLM summary of a session, shown above the timeline."""

    def __init__(self):
        super().__init__()
        lay = vbox(spacing=6, parent=self)
        lay.addWidget(theme.label('SUMMARY', 'section'))
        self.text = theme.label('', 'summary', wrap=True, selectable=True)
        self.text.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.text)

    def set_summary(self, summary):
        self.text.setText((summary or '').strip())
        self.setVisible(bool(summary))

