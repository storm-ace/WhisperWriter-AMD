"""
Main window: session history on the left, the selected session's dictations in the
middle with the record dock, and Claude Code / engine context on the right. Settings
open as a page in the same window.
"""

import os
import time

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QScrollArea, QSizePolicy, QStackedWidget,
                               QVBoxLayout, QWidget)

from ui import theme
from ui.palette import CommandPalette
from ui.settings_page import SettingsPage
from ui.widgets import ChipRow, Toast, Toggle, Waveform
from utils import ConfigManager

RECORDING_MODES = [
    ('continuous', 'Continuous'),
    ('voice_activity_detection', 'Voice detect'),
    ('press_to_toggle', 'Toggle'),
    ('hold_to_record', 'Hold'),
]


def fmt_when(ts):
    t = time.localtime(ts)
    now = time.localtime()
    if t.tm_year == now.tm_year and t.tm_yday == now.tm_yday:
        return time.strftime('%H:%M', t)
    if t.tm_year == now.tm_year and now.tm_yday - t.tm_yday == 1:
        return 'Yesterday'
    return time.strftime('%b %d', t)


def day_group(ts):
    t = time.localtime(ts)
    now = time.localtime()
    if t.tm_year == now.tm_year and t.tm_yday == now.tm_yday:
        return 'TODAY'
    if t.tm_year == now.tm_year and now.tm_yday - t.tm_yday == 1:
        return 'YESTERDAY'
    if time.time() - ts < 7 * 86400:
        return 'THIS WEEK'
    return 'EARLIER'


def fmt_duration(seconds):
    seconds = int(seconds or 0)
    if seconds >= 3600:
        return f'{seconds // 3600}h {seconds % 3600 // 60}m'
    if seconds >= 60:
        return f'{seconds // 60} min'
    return f'{seconds} s'


def plural(n, word):
    return f'{n:,} {word}{"" if n == 1 else "s"}'


def hotkey_parts(combo):
    return [p.strip().capitalize() for p in (combo or '').split('+') if p.strip()]


def vbox(margins=(0, 0, 0, 0), spacing=0, parent=None):
    lay = QVBoxLayout(parent) if parent is not None else QVBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


def hbox(margins=(0, 0, 0, 0), spacing=0, parent=None):
    lay = QHBoxLayout(parent) if parent is not None else QHBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


class SessionItem(QWidget):
    def __init__(self, title, meta, active=False, snippet=None):
        super().__init__()
        lay = vbox((12, 9, 12, 9), 3, self)
        top = hbox(spacing=8)
        if active:
            top.addWidget(theme.dot(theme.ACCENT, 6))
        t = QLabel()
        t.setStyleSheet('font-weight: 500;')
        t.setTextFormat(Qt.TextFormat.PlainText)
        t.setText(t.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, 200 if active else 212))
        t.setToolTip(title)
        top.addWidget(t, 1)
        lay.addLayout(top)
        if snippet:
            s = theme.label(snippet, 'faint', wrap=True)
            lay.addWidget(s)
        m = theme.label('', 'faint')
        m.setText(m.fontMetrics().elidedText(meta, Qt.TextElideMode.ElideRight, 212))
        lay.addWidget(m)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


class EntryWidget(QFrame):
    copyRequested = Signal(str)
    deleteRequested = Signal(int)

    def __init__(self, d):
        super().__init__()
        self.setObjectName('Entry')
        self.dictation_id = d['id']
        lay = hbox((0, 16, 0, 16), 16, self)

        stamp = time.strftime('%H:%M', time.localtime(d['created_at']))
        dur = d.get('duration_s')
        left = theme.label(f"{stamp}\n{int(dur) // 60}:{int(dur) % 60:02d}" if dur else stamp,
                           'mono')
        left.setFixedWidth(56)
        left.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addWidget(left)

        right = vbox(spacing=8)
        head = hbox(spacing=6)
        chips = []
        if d.get('target_app'):
            chips.append(('chip', f"→ {d['target_app']}"))
        if d.get('latency_s'):
            chips.append(('chip', f"{d['latency_s']:.1f} s"))
        if d.get('corrected'):
            chips.append(('chip-info', 'LLM corrected'))
        for role, text in chips:
            head.addWidget(theme.label(text, role))
        head.addStretch(1)
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


class MainWindow(QMainWindow):
    recordClicked = Signal()
    newSessionRequested = Signal()
    continueSessionRequested = Signal(int)
    settingsApplied = Signal(dict)   # previous config snapshot
    mcpSetupRequested = Signal()

    def __init__(self, store, mcp_status_fn=None):
        super().__init__()
        self.store = store
        self.mcp_status_fn = mcp_status_fn or (lambda: False)
        self.active_session_id = None
        self.viewed_session_id = None
        self.engine_state = ('starting', 'Starting engine…')
        self._entry_count = 0

        self.setWindowTitle('WhisperWriter')
        self.setWindowIcon(theme.app_icon())
        self.resize(1360, 860)
        self.setMinimumSize(760, 560)

        root = QWidget()
        root.setObjectName('Root')
        self.setCentralWidget(root)
        lay = vbox(parent=root)
        lay.addWidget(self._build_top_bar())

        self.pages = QStackedWidget()
        lay.addWidget(self.pages, 1)
        self.pages.addWidget(self._build_sessions_page())
        self.settings_page = SettingsPage(self)
        self.settings_page.closed.connect(self.show_sessions)
        self.settings_page.saved.connect(self._on_settings_saved)
        self.settings_page.mcpSetupRequested.connect(self.mcpSetupRequested)
        self.pages.addWidget(self.settings_page)

        self.toast = Toast(root)
        self.palette = None

        QShortcut(QKeySequence('Ctrl+K'), self, activated=self.open_palette)
        QShortcut(QKeySequence('Ctrl+N'), self, activated=self.newSessionRequested)
        QShortcut(QKeySequence('Ctrl+F'), self, activated=self._focus_search)
        QShortcut(QKeySequence('Ctrl+,'), self, activated=self.show_settings)
        QShortcut(QKeySequence('Escape'), self, activated=self._on_escape)

        self._search_timer = QTimer(self, singleShot=True, interval=180,
                                    timeout=self.refresh_sessions)
        self.refresh_config_views()

    # ================================================================ top bar
    def _build_top_bar(self):
        bar = QWidget()
        bar.setObjectName('TopBar')
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = hbox((20, 10, 14, 10), 12, bar)

        logo = QLabel()
        logo.setPixmap(theme.icon('logo', theme.ACCENT).pixmap(22, 22))
        lay.addWidget(logo)
        lay.addWidget(theme.label('WhisperWriter', 'brand'))
        lay.addWidget(theme.label('AMD', 'chip'))
        lay.addSpacing(8)

        self.status_label = theme.label('Starting engine…', 'status')
        self.status_label.setProperty('state', 'starting')
        lay.addWidget(self.status_label)
        self.speed_label = theme.label('', 'mono')
        lay.addWidget(self.speed_label)
        lay.addStretch(1)

        self.hotkey_box = QWidget()
        self.hotkey_lay = hbox(spacing=5, parent=self.hotkey_box)
        lay.addWidget(self.hotkey_box)

        palette_btn = theme.button('  Ctrl K', None, 'search', 'Search and commands (Ctrl+K)')
        palette_btn.clicked.connect(self.open_palette)
        lay.addWidget(palette_btn)
        settings_btn = theme.button('', None, 'settings', 'Settings (Ctrl+,)')
        settings_btn.setFixedWidth(40)
        settings_btn.clicked.connect(self.show_settings)
        lay.addWidget(settings_btn)
        return bar

    # ================================================================ sessions page
    def _build_sessions_page(self):
        page = QWidget()
        lay = hbox(parent=page)
        lay.addWidget(self._build_sidebar())
        lay.addWidget(self._build_center(), 1)
        self.context_panel = self._build_context_panel()
        lay.addWidget(self.context_panel)
        return page

    def _build_sidebar(self):
        side = QWidget()
        side.setObjectName('Sidebar')
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        side.setFixedWidth(276)
        lay = vbox((12, 16, 12, 12), 12, side)

        new_btn = theme.button('New session', 'primary', 'plus', 'New session (Ctrl+N)')
        new_btn.clicked.connect(self.newSessionRequested)
        lay.addWidget(new_btn)

        self.search = QLineEdit()
        self.search.setProperty('role', 'search')
        self.search.setPlaceholderText('Search all transcripts…')
        self.search.setClearButtonEnabled(True)
        self.search.addAction(theme.icon('search', theme.FAINT), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setAccessibleName('Search sessions')
        self.search.textChanged.connect(lambda: self._search_timer.start())
        lay.addWidget(self.search)

        self.session_list = QListWidget()
        self.session_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.session_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.session_list.itemClicked.connect(self._on_session_clicked)
        self.session_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.session_list.customContextMenuRequested.connect(self._session_context_menu)
        lay.addWidget(self.session_list, 1)

        foot = QWidget()
        foot.setStyleSheet(f'background: {theme.SURFACE}; border-radius: 8px;')
        flay = hbox((10, 8, 10, 8), 8, foot)
        db_icon = QLabel()
        db_icon.setPixmap(theme.icon('db', theme.MUTED).pixmap(14, 14))
        flay.addWidget(db_icon)
        self.storage_label = theme.label('', 'faint')
        flay.addWidget(self.storage_label, 1)
        lay.addWidget(foot)
        return side

    def _build_center(self):
        center = QWidget()
        center.setObjectName('Center')
        center.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = vbox(parent=center)

        # header
        header = QWidget()
        hl = hbox((32, 24, 32, 8), 16, header)
        titles = vbox(spacing=4)
        self.title_stack = QStackedWidget()
        self.title_label = theme.label('', 'h1')
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.title_label.setMinimumWidth(1)
        self.title_label.mouseDoubleClickEvent = lambda e: self._start_rename()
        self.title_edit = QLineEdit()
        self.title_edit.setProperty('role', 'title-edit')
        self.title_edit.editingFinished.connect(self._finish_rename)
        self.title_stack.addWidget(self.title_label)
        self.title_stack.addWidget(self.title_edit)
        self.title_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.title_stack.setFixedHeight(40)
        titles.addWidget(self.title_stack)
        self.meta_label = theme.label('', 'muted', wrap=True)
        titles.addWidget(self.meta_label)
        hl.addLayout(titles, 1)

        self.continue_btn = theme.button('Continue here', None, 'play',
                                         'Make this the session new dictations go to')
        self.continue_btn.clicked.connect(
            lambda: self.viewed_session_id and self.continueSessionRequested.emit(self.viewed_session_id))
        hl.addWidget(self.continue_btn, 0, Qt.AlignmentFlag.AlignTop)
        copy_btn = theme.button('Copy as Markdown', None, 'copy')
        copy_btn.clicked.connect(lambda: self.copy_session(for_claude=False))
        hl.addWidget(copy_btn, 0, Qt.AlignmentFlag.AlignTop)
        claude_btn = theme.button('Copy for Claude Code', 'warm', 'terminal',
                                  'Copy this session as a context block to paste into Claude Code',
                                  icon_color='#FFD3BF')
        claude_btn.clicked.connect(lambda: self.copy_session(for_claude=True))
        hl.addWidget(claude_btn, 0, Qt.AlignmentFlag.AlignTop)
        more = theme.button('', None, 'more', 'More actions')
        more.setFixedWidth(40)
        more.clicked.connect(lambda: self._session_menu(self.viewed_session_id).exec(
            more.mapToGlobal(more.rect().bottomLeft())))
        hl.addWidget(more, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(header)

        # timeline
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self.timeline = vbox((32, 4, 32, 16), 0, holder)
        self.empty_label = theme.label('', 'muted', wrap=True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setContentsMargins(0, 80, 0, 0)
        self.timeline.addWidget(self.empty_label)
        self.timeline.addStretch(1)
        self.scroll.setWidget(holder)
        lay.addWidget(self.scroll, 1)

        lay.addWidget(self._build_dock())
        return center

    def _build_dock(self):
        wrap = QWidget()
        wl = hbox((24, 0, 24, 20), parent=wrap)
        dock = QFrame()
        dock.setObjectName('Dock')
        wl.addWidget(dock)
        lay = hbox((14, 12, 14, 12), 16, dock)

        self.record_btn = theme.button('', 'record', None, 'Start dictating into this session')
        self.record_btn.setIcon(theme.icon('mic', theme.ACCENT_INK))
        self.record_btn.setIconSize(QSize(22, 22))
        self.record_btn.clicked.connect(self.recordClicked)
        lay.addWidget(self.record_btn)

        mid = vbox(spacing=4)
        self.dock_wave = Waveform(bars=34)
        mid.addWidget(self.dock_wave)
        self.dock_label = theme.label('', 'faint')
        mid.addWidget(self.dock_label)
        lay.addLayout(mid, 1)

        group = QFrame()
        group.setObjectName('ModeGroup')
        gl = hbox((3, 3, 3, 3), 2, group)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        for key, text in RECORDING_MODES:
            b = theme.button(text, 'mode')
            b.setCheckable(True)
            b.setProperty('mode_key', key)
            self.mode_group.addButton(b)
            gl.addWidget(b)
        self.mode_group.buttonClicked.connect(self._on_mode_clicked)
        lay.addWidget(group)
        return wrap

    def _build_context_panel(self):
        panel = QWidget()
        panel.setObjectName('ContextPanel')
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel.setFixedWidth(312)
        outer = vbox(parent=panel)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        lay = vbox((18, 20, 18, 20), 20, inner)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        # Claude Code link
        sec = vbox(spacing=10)
        sec.addWidget(theme.label('CLAUDE CODE LINK', 'section'))
        card = QFrame()
        card.setObjectName('Card')
        cl = vbox((12, 12, 12, 12), 8, card)
        head = hbox(spacing=8)
        self.mcp_dot = theme.dot(theme.INFO)
        head.addWidget(self.mcp_dot)
        self.mcp_title = QLabel()
        self.mcp_title.setStyleSheet('font-weight: 500;')
        head.addWidget(self.mcp_title, 1)
        cl.addLayout(head)
        self.mcp_desc = theme.label('', 'faint', wrap=True)
        cl.addWidget(self.mcp_desc)
        cl.addWidget(ChipRow(['search_dictations', 'get_session', 'list_sessions',
                              'recent_dictations']))
        self.mcp_btn = theme.button('Set up', None, 'terminal')
        self.mcp_btn.clicked.connect(lambda: self.show_settings('claude'))
        cl.addWidget(self.mcp_btn)
        sec.addWidget(card)
        lay.addLayout(sec)

        # Smart dictation toggles (saved immediately)
        sec = vbox(spacing=12)
        sec.addWidget(theme.label('SMART DICTATION', 'section'))
        self.quick_toggles = {}
        for keys, text in [
            (('claude_code', 'smart_terminal_mode'), 'Claude Code aware typing'),
            (('claude_code', 'backtick_filenames'), 'Wrap file names in backticks'),
            (('claude_code', 'suppress_enter'), 'Never press Enter in Claude Code'),
            (('post_processing', 'llm_correction', 'enabled'), 'LLM correction (local)'),
        ]:
            row = hbox(spacing=12)
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            row.addWidget(lbl, 1)
            t = Toggle()
            t.setAccessibleName(text)
            t.toggled.connect(lambda checked, k=keys: self._quick_toggle(k, checked))
            row.addWidget(t)
            self.quick_toggles[keys] = t
            sec.addLayout(row)
        lay.addLayout(sec)

        # Vocabulary
        sec = vbox(spacing=10)
        head = hbox()
        head.addWidget(theme.label('PROJECT VOCABULARY', 'section'), 1)
        edit = theme.button('Edit', 'ghost')
        edit.setFixedHeight(26)
        edit.clicked.connect(lambda: self.show_settings('correction'))
        head.addWidget(edit)
        sec.addLayout(head)
        self.vocab_chips = ChipRow()
        sec.addWidget(self.vocab_chips)
        self.vocab_hint = theme.label('Fed to Whisper as a prompt so names land right the '
                                      'first time.', 'faint', wrap=True)
        sec.addWidget(self.vocab_hint)
        lay.addLayout(sec)

        # Engine stats
        card = QFrame()
        card.setObjectName('Card')
        el = vbox((12, 12, 12, 12), 8, card)
        el.addWidget(theme.label('ENGINE', 'section'))
        self.stat_rows = {}
        for key in ('Engine', 'Model', 'Avg latency', 'Avg speed'):
            row = hbox()
            row.addWidget(QLabel(key), 1)
            value = theme.label('—', 'mono')
            value.setStyleSheet(f'color: {theme.TEXT};')
            row.addWidget(value)
            self.stat_rows[key] = value
            el.addLayout(row)
        lay.addWidget(card)
        lay.addStretch(1)
        return panel

    # ================================================================ data views
    def refresh_sessions(self):
        query = self.search.text().strip()
        self.session_list.clear()
        if query:
            hits = self.store.search_sessions(query, limit=60)
            self._add_group_header(f'{len(hits)} MATCHING SESSIONS')
            for h in hits:
                snippet = (h['snippet'] or '').replace('[', '').replace(']', '')
                self._add_session_item(h['session_id'], h['session_title'] or 'Untitled session',
                                       fmt_when(h['created_at']), snippet=f'“{snippet}”')
        else:
            current = None
            for s in self.store.list_sessions(limit=300):
                if s['dictation_count'] == 0 and s['id'] != self.active_session_id:
                    continue
                group = day_group(s['updated_at'])
                if group != current:
                    self._add_group_header(group)
                    current = group
                parts = [p for p in (s.get('last_app'), plural(s['dictation_count'], 'dictation'),
                                     fmt_when(s['updated_at'])) if p]
                self._add_session_item(s['id'], s['title'] or 'New session', ' · '.join(parts),
                                       active=s['id'] == self.active_session_id)
        stats = self.store.stats()
        n = stats['session_count']
        self.storage_label.setText(f"{n} session{'s' if n != 1 else ''} · stored locally")

    def _add_group_header(self, text):
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        w = theme.label(text, 'section')
        w.setContentsMargins(12, 12, 0, 4)
        item.setSizeHint(w.sizeHint())
        self.session_list.addItem(item)
        self.session_list.setItemWidget(item, w)

    def _add_session_item(self, session_id, title, meta, active=False, snippet=None):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, session_id)
        w = SessionItem(title, meta, active, snippet)
        w.setFixedWidth(240)
        item.setSizeHint(QSize(240, w.sizeHint().height()))
        self.session_list.addItem(item)
        self.session_list.setItemWidget(item, w)
        if session_id == self.viewed_session_id:
            item.setSelected(True)

    def _on_session_clicked(self, item):
        sid = item.data(Qt.ItemDataRole.UserRole)
        if sid:
            self.show_session(sid)

    def set_active_session(self, session_id):
        self.active_session_id = session_id
        self.show_session(session_id)
        self.refresh_sessions()

    def show_session(self, session_id):
        self.viewed_session_id = session_id
        self.title_stack.setCurrentIndex(0)
        while self.timeline.count() > 2:  # keep empty label + stretch
            item = self.timeline.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        self._entry_count = 0
        session = self.store.get_session(session_id) if session_id else None
        if session:
            for d in self.store.dictations(session_id, limit=400):
                self._append_entry(d)
        self._update_header(session)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def add_dictation(self, session_id, dictation):
        """Called by the app after a dictation was stored."""
        if session_id == self.viewed_session_id:
            self._append_entry(dictation)
            self._update_header(self.store.get_session(session_id))
            QTimer.singleShot(30, self._scroll_to_bottom)
        self.refresh_sessions()
        self.refresh_stats()

    def _append_entry(self, d):
        entry = EntryWidget(d)
        entry.copyRequested.connect(self._copy_text)
        entry.deleteRequested.connect(self._delete_dictation)
        self.timeline.insertWidget(self.timeline.count() - 1, entry)
        self._entry_count += 1
        self.empty_label.setVisible(False)

    def _update_header(self, session):
        if not session:
            self._set_title('No session')
            self.meta_label.setText('')
        else:
            self._set_title(session['title'] or 'New session')
            started = time.strftime('%H:%M', time.localtime(session['created_at']))
            day = fmt_when(session['created_at'])
            started = started if day == started else f'{day} {started}'
            self.meta_label.setText(
                f"Started {started}  ·  {plural(session['dictation_count'], 'dictation')}  ·  "
                f"{plural(session['word_count'], 'word')}  ·  {fmt_duration(session['audio_s'])} audio")
        is_active = session and session['id'] == self.active_session_id
        self.continue_btn.setVisible(bool(session) and not is_active)
        if self._entry_count == 0:
            keys = ' + '.join(hotkey_parts(
                ConfigManager.get_config_value('recording_options', 'activation_key')))
            self.empty_label.setText(
                f'No dictations yet.\nPress {keys} in any app and start talking — '
                'everything you say is saved here.')
            self.empty_label.setVisible(True)

    def _set_title(self, title):
        self._full_title = title
        self._elide_title()

    def _elide_title(self):
        title = getattr(self, '_full_title', '')
        width = max(120, self.title_stack.width())
        self.title_label.setText(
            self.title_label.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, width))
        self.title_label.setToolTip(f'{title} (double-click to rename)')

    def _scroll_to_bottom(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ================================================================ session actions
    def _session_menu(self, session_id):
        menu = QMenu(self)
        if not session_id:
            return menu
        menu.addAction(theme.icon('edit', theme.MUTED), 'Rename',
                       lambda: (self.show_session(session_id), self._start_rename()))
        menu.addAction(theme.icon('terminal', theme.MUTED), 'Copy for Claude Code',
                       lambda: self.copy_session(True, session_id))
        menu.addAction(theme.icon('copy', theme.MUTED), 'Copy as Markdown',
                       lambda: self.copy_session(False, session_id))
        menu.addAction(theme.icon('download', theme.MUTED), 'Export Markdown…',
                       lambda: self.export_session(session_id))
        menu.addSeparator()
        menu.addAction(theme.icon('trash', theme.DANGER), 'Delete session',
                       lambda: self.delete_session(session_id))
        return menu

    def _session_context_menu(self, pos):
        item = self.session_list.itemAt(pos)
        sid = item.data(Qt.ItemDataRole.UserRole) if item else None
        if sid:
            self._session_menu(sid).exec(self.session_list.viewport().mapToGlobal(pos))

    def copy_session(self, for_claude, session_id=None):
        sid = session_id or self.viewed_session_id
        if not sid:
            return
        QGuiApplication.clipboard().setText(self.store.session_markdown(sid, for_claude=for_claude))
        self.toast.show_message('Copied — paste it into Claude Code' if for_claude
                                else 'Session copied as Markdown')

    def export_session(self, session_id):
        session = self.store.get_session(session_id)
        name = ''.join(c if c.isalnum() or c in ' -_' else '' for c in (session['title'] or 'session'))
        path, _ = QFileDialog.getSaveFileName(self, 'Export session',
                                              os.path.join(os.path.expanduser('~'), f'{name.strip()}.md'),
                                              'Markdown (*.md)')
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(self.store.session_markdown(session_id))
            self.toast.show_message(f'Exported to {os.path.basename(path)}')

    def delete_session(self, session_id):
        session = self.store.get_session(session_id)
        if not session:
            return
        answer = QMessageBox.question(
            self, 'Delete session?',
            f"Delete “{session['title'] or 'New session'}” and its {session['dictation_count']} "
            'dictations? This cannot be undone.')
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.delete_session(session_id)
        if session_id == self.active_session_id:
            self.newSessionRequested.emit()
        else:
            if session_id == self.viewed_session_id:
                self.show_session(self.active_session_id)
            self.refresh_sessions()

    def _delete_dictation(self, dictation_id):
        self.store.delete_dictation(dictation_id)
        self.show_session(self.viewed_session_id)
        self.refresh_sessions()

    def _copy_text(self, text):
        QGuiApplication.clipboard().setText(text)
        self.toast.show_message('Copied')

    def _start_rename(self):
        if not self.viewed_session_id:
            return
        self.title_edit.setText(getattr(self, '_full_title', self.title_label.text()))
        self.title_stack.setCurrentIndex(1)
        self.title_edit.setFocus()
        self.title_edit.selectAll()

    def _finish_rename(self):
        if self.title_stack.currentIndex() != 1:
            return
        self.title_stack.setCurrentIndex(0)
        if self.viewed_session_id and self.title_edit.text().strip():
            self.store.rename_session(self.viewed_session_id, self.title_edit.text())
            self._set_title(self.title_edit.text().strip())
            self.refresh_sessions()

    # ================================================================ status
    def set_engine_status(self, state, text, detail=None):
        self.engine_state = (state, text)
        self.status_label.setText(text)
        self.status_label.setProperty('state', state)
        theme.restyle(self.status_label)
        if detail is not None:
            self.speed_label.setText(detail)
        self._update_dock_label()

    def set_recording_state(self, status):
        recording = status == 'recording'
        self.record_btn.setProperty('recording', 'true' if recording else 'false')
        self.record_btn.setIcon(theme.icon('stop' if recording else 'mic', theme.ACCENT_INK))
        self.record_btn.setToolTip('Stop' if recording else 'Start dictating into this session')
        theme.restyle(self.record_btn)
        self.dock_wave.active = recording
        if not recording:
            self.dock_wave.reset()
        if status in ('recording', 'transcribing'):
            self.status_label.setText('Listening…' if recording else 'Transcribing…')
            self.status_label.setProperty('state', 'recording')
            theme.restyle(self.status_label)
        else:
            self.set_engine_status(*self.engine_state)

    def push_level(self, level):
        self.dock_wave.push(level)

    def set_last_target(self, label):
        self._last_target = label
        self._update_dock_label()

    def _update_dock_label(self):
        target = getattr(self, '_last_target', '')
        device = ConfigManager.get_config_value('recording_options', 'sound_device') or 'default mic'
        text = f'Last typed into {target}' if target else 'Record button → this session only'
        self.dock_label.setText(f'{text}  ·  mic: {device}')

    def refresh_stats(self):
        stats = self.store.stats()
        lat, dur = stats['avg_latency'], stats['avg_duration']
        self.stat_rows['Avg latency'].setText(f'{lat:.2f} s' if lat else '—')
        speed = f'{dur / lat:.0f}× realtime' if lat and dur else ''
        self.stat_rows['Avg speed'].setText(speed or '—')
        self.speed_label.setText(speed)

    def refresh_config_views(self):
        """Re-read config-driven bits of the UI (after load or settings save)."""
        rec = ConfigManager.get_config_section('recording_options')
        while self.hotkey_lay.count():
            w = self.hotkey_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.hotkey_lay.addWidget(theme.label('Dictate', 'faint'))
        for part in hotkey_parts(rec.get('activation_key')):
            k = theme.label(part, 'kbd')
            k.setFixedHeight(22)
            self.hotkey_lay.addWidget(k)

        mode = rec.get('recording_mode') or 'continuous'
        for b in self.mode_group.buttons():
            b.setChecked(b.property('mode_key') == mode)

        for keys, toggle in self.quick_toggles.items():
            toggle.blockSignals(True)
            toggle.setChecked(bool(ConfigManager.get_config_value(*keys)))
            toggle.blockSignals(False)

        vocab = ConfigManager.get_config_value('model_options', 'common', 'vocabulary') or ''
        words = [w.strip() for w in vocab.replace('\n', ',').split(',') if w.strip()]
        shown = words[:12] + ([f'+ {len(words) - 12}'] if len(words) > 12 else [])
        self.vocab_chips.set_chips(shown or ['none yet'])

        engine = ConfigManager.get_config_value('model_options', 'engine')
        if engine == 'whispercpp':
            model = ConfigManager.get_config_value('model_options', 'whispercpp', 'model_path') or ''
            self.stat_rows['Engine'].setText('whisper.cpp · Vulkan')
            self.stat_rows['Model'].setText(os.path.basename(model).replace('ggml-', '').replace('.bin', '') or '—')
        else:
            self.stat_rows['Engine'].setText('faster-whisper · CPU')
            self.stat_rows['Model'].setText(ConfigManager.get_config_value('model_options', 'local', 'model') or '—')
        self.refresh_mcp_status()
        self.refresh_stats()
        self._update_dock_label()

    def refresh_mcp_status(self):
        registered = self.mcp_status_fn()
        self.mcp_dot.setStyleSheet(f"background: {theme.INFO if registered else theme.FAINT}; border-radius: 4px;")
        self.mcp_title.setText('MCP server registered' if registered else 'MCP server not set up')
        self.mcp_desc.setText(
            'Claude Code can search and read your sessions with the whisperwriter tools.'
            if registered else
            'Register the whisperwriter MCP server so Claude Code can look back through your '
            'dictations for context.')
        self.mcp_btn.setVisible(not registered)

    def _quick_toggle(self, keys, checked):
        old = ConfigManager.snapshot()
        ConfigManager.set_config_value(checked, *keys)
        ConfigManager.save_config()
        self.settings_page.load_from_config()
        self.settingsApplied.emit(old)

    def _on_mode_clicked(self, button):
        old = ConfigManager.snapshot()
        ConfigManager.set_config_value(button.property('mode_key'), 'recording_options', 'recording_mode')
        ConfigManager.save_config()
        self.settings_page.load_from_config()
        self.settingsApplied.emit(old)

    def _on_settings_saved(self, old):
        self.refresh_config_views()
        self.settingsApplied.emit(old)
        self.toast.show_message('Settings saved and applied')

    # ================================================================ navigation
    def show_settings(self, section=None):
        self.settings_page.load_from_config()
        self.pages.setCurrentIndex(1)
        if section:
            QTimer.singleShot(0, lambda: self.settings_page.scroll_to(section))
        self.bring_to_front()

    def show_sessions(self):
        self.pages.setCurrentIndex(0)

    def bring_to_front(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def open_palette(self):
        self.bring_to_front()
        actions = [
            ('New session', 'plus', 'Ctrl N', self.newSessionRequested.emit),
            ('Copy current session for Claude Code', 'terminal', '',
             lambda: self.copy_session(True)),
            ('Copy current session as Markdown', 'copy', '', lambda: self.copy_session(False)),
            ('Start / stop dictation', 'mic', '', self.recordClicked.emit),
            ('Rename current session', 'edit', '', self._start_rename),
            ('Export current session…', 'download', '',
             lambda: self.viewed_session_id and self.export_session(self.viewed_session_id)),
            ('Open settings', 'settings', 'Ctrl ,', self.show_settings),
            ('Set up Claude Code (MCP)', 'terminal', '', lambda: self.show_settings('claude')),
            ('Edit project vocabulary', 'sparkle', '', lambda: self.show_settings('correction')),
        ]
        self.palette = CommandPalette(self, self.store, actions, self.show_session)
        self.palette.open_centered()

    def _focus_search(self):
        self.show_sessions()
        self.search.setFocus()
        self.search.selectAll()

    def _on_escape(self):
        if self.pages.currentIndex() == 1:
            self.settings_page.request_close()
        elif self.search.text():
            self.search.clear()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.context_panel.setVisible(self.width() >= 1180)
        QTimer.singleShot(0, self._elide_title)

    def closeEvent(self, event):
        # Closing the window keeps WhisperWriter running in the tray.
        event.ignore()
        self.hide()
