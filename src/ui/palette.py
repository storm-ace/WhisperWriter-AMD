"""Ctrl+K command palette: run actions and jump into sessions by full-text search."""

import time

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QVBoxLayout, QWidget)

from ui import theme


class _Row(QWidget):
    def __init__(self, icon_name, title, subtitle='', hint='', accent=False):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(12)
        ic = QLabel()
        ic.setPixmap(theme.icon(icon_name, theme.ACCENT if accent else theme.MUTED).pixmap(16, 16))
        lay.addWidget(ic)
        text = QVBoxLayout()
        text.setSpacing(2)
        t = QLabel(title)
        t.setTextFormat(Qt.TextFormat.PlainText)
        text.addWidget(t)
        if subtitle:
            s = theme.label(subtitle, 'faint')
            s.setTextFormat(Qt.TextFormat.RichText)
            text.addWidget(s)
        lay.addLayout(text, 1)
        if hint:
            lay.addWidget(theme.label(hint, 'kbd' if any(k in hint for k in ('Ctrl', 'Enter')) else 'mono'))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def _highlight(snippet):
    import html
    s = html.escape(snippet or '')
    return (s.replace('[', f'<span style="background:#3A2416;color:#FFD3BF;">')
             .replace(']', '</span>'))


class CommandPalette(QDialog):
    def __init__(self, parent, store, actions, open_session):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.store = store
        self.actions = actions
        self.open_session = open_session

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QFrame()
        box.setObjectName('Palette')
        outer.addWidget(box)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        top = QHBoxLayout()
        top.setContentsMargins(16, 0, 16, 0)
        top.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(theme.icon('search', theme.MUTED).pixmap(16, 16))
        top.addWidget(ic)
        self.input = QLineEdit()
        self.input.setProperty('role', 'palette')
        self.input.setPlaceholderText('Search sessions or type a command…')
        self.input.setAccessibleName('Search commands and sessions')
        self.input.textChanged.connect(self.refresh)
        self.input.installEventFilter(self)
        top.addWidget(self.input, 1)
        esc = theme.label('Esc', 'kbd')
        esc.setFixedHeight(22)
        top.addWidget(esc)
        lay.addLayout(top)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f'background: {theme.BORDER};')
        lay.addWidget(line)

        self.list = QListWidget()
        self.list.setContentsMargins(8, 8, 8, 8)
        self.list.setStyleSheet('QListWidget { padding: 6px 8px; }')
        self.list.itemActivated.connect(self._run)
        self.list.itemClicked.connect(self._run)
        lay.addWidget(self.list, 1)

        foot = QHBoxLayout()
        foot.setContentsMargins(16, 8, 16, 10)
        foot.setSpacing(16)
        for text in ('↑ ↓ navigate', 'Enter open', 'Esc close'):
            foot.addWidget(theme.label(text, 'faint'))
        foot.addStretch(1)
        lay.addLayout(foot)

        self.resize(680, 460)
        self.refresh()

    def open_centered(self):
        parent = self.parentWidget()
        geo = parent.geometry()
        self.move(geo.x() + (geo.width() - self.width()) // 2, geo.y() + 90)
        self.show()
        self.input.setFocus()

    def _header(self, text):
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        w = theme.label(text, 'section')
        w.setContentsMargins(12, 10, 0, 4)
        item.setSizeHint(w.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, w)

    def _row(self, widget, payload):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, payload)
        item.setSizeHint(QSize(600, max(44, widget.sizeHint().height())))
        self.list.addItem(item)
        self.list.setItemWidget(item, widget)
        return item

    def refresh(self):
        query = self.input.text().strip()
        self.list.clear()
        first = None

        if query:
            hits = self.store.search_sessions(query, limit=6)
            if hits:
                self._header(f'SESSIONS · {len(hits)} MATCH{"ES" if len(hits) != 1 else ""}')
                for h in hits:
                    when = time.strftime('%b %d', time.localtime(h['created_at']))
                    row = _Row('list', h['session_title'] or 'Untitled session',
                               f'“{_highlight(h["snippet"])}”', when, accent=True)
                    item = self._row(row, ('session', h['session_id']))
                    first = first or item

        q = query.lower()
        matches = [a for a in self.actions if not q or q in a[0].lower()]
        if matches:
            self._header('ACTIONS')
            for title, icon_name, hint, fn in matches:
                item = self._row(_Row(icon_name, title, hint=hint), ('action', fn))
                first = first or item

        if self.list.count() == 0:
            self._header('NO RESULTS')
        if first:
            self.list.setCurrentItem(first)

    def _run(self, item):
        payload = item.data(Qt.ItemDataRole.UserRole)
        if not payload:
            return
        kind, value = payload
        self.close()
        if kind == 'session':
            self.parentWidget().show_sessions()
            self.open_session(value)
        else:
            value()

    def _move(self, step):
        row = self.list.currentRow()
        for _ in range(self.list.count()):
            row = (row + step) % self.list.count()
            if self.list.item(row).flags() & Qt.ItemFlag.ItemIsEnabled:
                self.list.setCurrentRow(row)
                return

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                self._move(1)
                return True
            if key == Qt.Key.Key_Up:
                self._move(-1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self.list.currentItem():
                    self._run(self.list.currentItem())
                return True
            if key == Qt.Key.Key_Escape:
                self.close()
                return True
        return super().eventFilter(obj, event)
