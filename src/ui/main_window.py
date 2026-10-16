"""
Main window: session history on the left, the selected session's dictations in the
middle with the record dock, and Claude Code / engine context on the right. Settings
open as a page in the same window.

The window only shows state and reports user intent through signals; the app object
(main.py) owns recording, engines and config changes.
"""

import os
import time

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QFrame, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox, QScrollArea,
                               QSizePolicy, QStackedWidget, QWidget)

from result_thread import Status
from ui import theme
from ui.context_panel import ContextPanel
from ui.formatting import day_group, fmt_duration, fmt_when, hotkey_parts, plural
from ui.palette import CommandPalette
from ui.session_widgets import EntryWidget, SessionItem, SummaryBox
from ui.settings_page import SettingsPage
from ui.widgets import Toast, Waveform, hbox, vbox
from utils import ConfigManager

RECORDING_MODES = [
    ('continuous', 'Continuous'),
    ('voice_activity_detection', 'Voice detect'),
    ('press_to_toggle', 'Toggle'),
    ('hold_to_record', 'Hold'),
]


class MainWindow(QMainWindow):
    recordClicked = Signal()
    newSessionRequested = Signal()
    continueSessionRequested = Signal(int)
    sendToClaudeRequested = Signal(int)
    summarizeRequested = Signal(int)
    settingChangeRequested = Signal(tuple, object)   # config keys, value (save + apply)
    settingsSaved = Signal(dict)                     # previous config snapshot
    mcpSetupRequested = Signal()
    restartEngineRequested = Signal()

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.active_session_id = None
        self.viewed_session_id = None
        self.engine_status = None
        self.last_target = ''
        self.full_title = ''
        self.entry_count = 0
        self.summarizing = set()
        self.palette = None

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
        self.settings_page.saved.connect(self.settingsSaved)
        self.settings_page.mcpSetupRequested.connect(self.mcpSetupRequested)
        self.settings_page.restartEngineRequested.connect(self.restartEngineRequested)
        self.pages.addWidget(self.settings_page)

        self.toast = Toast(root)

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
        self.context_panel = ContextPanel()
        self.context_panel.settingToggled.connect(
            lambda keys, value: self.settingChangeRequested.emit(keys, value))
        self.context_panel.openSettingsRequested.connect(self.show_settings)
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
        foot.setObjectName('StorageFoot')
        foot.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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
        lay.addWidget(self._build_header())

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self.timeline = vbox((32, 4, 32, 16), 0, holder)
        self.summary_box = SummaryBox()
        self.summary_box.setContentsMargins(0, 0, 0, 12)
        self.timeline.addWidget(self.summary_box)
        self.empty_label = theme.label('', 'muted', wrap=True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setContentsMargins(0, 80, 0, 0)
        self.timeline.addWidget(self.empty_label)
        self.timeline.addStretch(1)
        self.scroll.setWidget(holder)
        lay.addWidget(self.scroll, 1)

        lay.addWidget(self._build_dock())
        return center

    def _build_header(self):
        header = QWidget()
        hl = hbox((32, 24, 32, 8), 12, header)
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

        top = Qt.AlignmentFlag.AlignTop
        self.continue_btn = theme.button('Continue here', None, 'play',
                                         'Make this the session new dictations go to')
        self.continue_btn.clicked.connect(
            lambda: self.viewed_session_id and self.continueSessionRequested.emit(self.viewed_session_id))
        hl.addWidget(self.continue_btn, 0, top)
        copy_btn = theme.button('Copy as context', None, 'copy',
                                'Copy this session as a context block for a prompt')
        copy_btn.clicked.connect(lambda: self.copy_session(for_claude=True))
        hl.addWidget(copy_btn, 0, top)
        send_btn = theme.button('Send to Claude Code', 'warm', 'send',
                                'Paste this session into the Claude Code window as context')
        send_btn.clicked.connect(lambda: self.viewed_session_id and
                                 self.sendToClaudeRequested.emit(self.viewed_session_id))
        hl.addWidget(send_btn, 0, top)
        more = theme.button('', None, 'more', 'More actions')
        more.setFixedWidth(40)
        more.clicked.connect(lambda: self._session_menu(self.viewed_session_id).exec(
            more.mapToGlobal(more.rect().bottomLeft())))
        hl.addWidget(more, 0, top)
        return header

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
        self.mode_group.buttonClicked.connect(
            lambda b: self.settingChangeRequested.emit(('recording_options', 'recording_mode'),
                                                       b.property('mode_key')))
        lay.addWidget(group)
        return wrap

    # ================================================================ sessions list
    def set_store(self, store):
        self.store = store

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
        n = self.store.stats()['session_count']
        self.storage_label.setText(f'{plural(n, "session")} · stored locally')

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

    # ================================================================ timeline
    def set_active_session(self, session_id):
        self.active_session_id = session_id
        self.show_session(session_id)
        self.refresh_sessions()

    def show_session(self, session_id):
        self.viewed_session_id = session_id
        self.title_stack.setCurrentIndex(0)
        while self.timeline.count() > 3:   # keep summary box, empty label and stretch
            item = self.timeline.takeAt(2)
            if item.widget():
                item.widget().deleteLater()
        self.entry_count = 0
        session = self.store.get_session(session_id) if session_id else None
        if session:
            for d in self.store.dictations(session_id, limit=400):
                self._append_entry(d)
        self._update_header(session)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def add_dictation(self, session_id, dictation):
        """Called after a dictation was stored; also follows an automatic session switch."""
        if session_id != self.active_session_id:
            self.active_session_id = session_id
            self.show_session(session_id)
        elif session_id == self.viewed_session_id:
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
        self.entry_count += 1
        self.empty_label.setVisible(False)

    def _update_header(self, session):
        if not session:
            self._set_title('No session')
            self.meta_label.setText('')
            self.summary_box.set_summary(None)
        else:
            self._set_title(session['title'] or 'New session')
            started = time.strftime('%H:%M', time.localtime(session['created_at']))
            day = fmt_when(session['created_at'])
            started = started if day == started else f'{day} {started}'
            self.meta_label.setText(
                f"Started {started}  ·  {plural(session['dictation_count'], 'dictation')}  ·  "
                f"{plural(session['word_count'], 'word')}  ·  {fmt_duration(session['audio_s'])} audio")
            self.summary_box.set_summary(session.get('summary'))
        is_active = bool(session) and session['id'] == self.active_session_id
        self.continue_btn.setVisible(bool(session) and not is_active)
        if self.entry_count == 0:
            keys = ' + '.join(hotkey_parts(
                ConfigManager.get_config_value('recording_options', 'activation_key')))
            self.empty_label.setText(
                f'No dictations yet.\nPress {keys} in any app and start talking — '
                'everything you say is saved here.')
            self.empty_label.setVisible(True)

    def _set_title(self, title):
        self.full_title = title
        self._elide_title()

    def _elide_title(self):
        width = max(120, self.title_stack.width())
        self.title_label.setText(self.title_label.fontMetrics().elidedText(
            self.full_title, Qt.TextElideMode.ElideRight, width))
        self.title_label.setToolTip(f'{self.full_title} (double-click to rename)')

    def _scroll_to_bottom(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def set_summary(self, session_id, summary):
        self.summarizing.discard(session_id)
        if session_id == self.viewed_session_id:
            self.summary_box.set_summary(summary)

    # ================================================================ session actions
    def _session_menu(self, session_id):
        menu = QMenu(self)
        if not session_id:
            return menu
        busy = session_id in self.summarizing
        menu.addAction(theme.icon('edit', theme.MUTED), 'Rename',
                       lambda: (self.show_session(session_id), self._start_rename()))
        summary = menu.addAction(theme.icon('sparkle', theme.MUTED),
                                 'Summarising…' if busy else 'Summarise with local LLM',
                                 lambda: self.request_summary(session_id))
        summary.setEnabled(not busy)
        menu.addAction(theme.icon('send', theme.MUTED), 'Send to Claude Code',
                       lambda: self.sendToClaudeRequested.emit(session_id))
        menu.addAction(theme.icon('copy', theme.MUTED), 'Copy as context',
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

    def request_summary(self, session_id=None):
        sid = session_id or self.viewed_session_id
        if not sid or sid in self.summarizing:
            return
        self.summarizing.add(sid)
        self.show_toast('Summarising with the local LLM…')
        self.summarizeRequested.emit(sid)

    def copy_session(self, for_claude, session_id=None):
        sid = session_id or self.viewed_session_id
        if not sid:
            return
        QGuiApplication.clipboard().setText(self.store.session_markdown(sid, for_claude=for_claude))
        self.show_toast('Copied — paste it into your prompt' if for_claude
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
            self.show_toast(f'Exported to {os.path.basename(path)}')

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
        self.show_toast('Copied')

    def _start_rename(self):
        if not self.viewed_session_id:
            return
        self.title_edit.setText(self.full_title)
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
    def set_engine_status(self, status):
        """Show an EngineStatus in the top bar, context panel and settings page."""
        self.engine_status = status
        self.status_label.setText(status.title)
        self.status_label.setProperty('state', status.state.value)
        theme.restyle(self.status_label)
        self.context_panel.set_engine(status)
        self.settings_page.set_engine_info(status)
        self._update_dock_label()

    def set_recording_state(self, status):
        recording = status == Status.RECORDING
        self.record_btn.setProperty('recording', 'true' if recording else 'false')
        self.record_btn.setIcon(theme.icon('stop' if recording else 'mic', theme.ACCENT_INK))
        self.record_btn.setToolTip('Stop' if recording else 'Start dictating into this session')
        theme.restyle(self.record_btn)
        self.dock_wave.active = recording
        if not recording:
            self.dock_wave.reset()
        if status in (Status.RECORDING, Status.TRANSCRIBING):
            self.status_label.setText('Listening…' if recording else 'Transcribing…')
            self.status_label.setProperty('state', 'recording')
            theme.restyle(self.status_label)
        elif self.engine_status:
            self.set_engine_status(self.engine_status)

    def push_level(self, level):
        self.dock_wave.push(level)

    def set_last_target(self, label):
        self.last_target = label
        self._update_dock_label()

    def _update_dock_label(self):
        device = ConfigManager.get_config_value('recording_options', 'sound_device') or 'default mic'
        text = f'Last typed into {self.last_target}' if self.last_target else 'Record button → this session only'
        self.dock_label.setText(f'{text}  ·  mic: {device}')

    def refresh_stats(self):
        stats = self.store.stats()
        lat, dur = stats['avg_latency'], stats['avg_duration']
        speed = f'{dur / lat:.0f}× realtime' if lat and dur else ''
        self.context_panel.set_speed(lat, speed)
        self.speed_label.setText(speed)

    def refresh_config_views(self):
        """Re-read config-driven bits of the UI (after load or a settings change)."""
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
        self.context_panel.refresh_config()
        self.settings_page.load_from_config()
        self.refresh_stats()
        self._update_dock_label()

    def refresh_mcp(self, registered, message=None):
        self.context_panel.set_mcp_registered(registered)
        self.settings_page.refresh_mcp(registered, message)

    def show_toast(self, message, ms=2400):
        self.toast.show_message(message, ms)

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
        sid = lambda: self.viewed_session_id   # noqa: E731
        actions = [
            ('New session', 'plus', 'Ctrl N', self.newSessionRequested.emit),
            ('Send current session to Claude Code', 'send', '',
             lambda: sid() and self.sendToClaudeRequested.emit(sid())),
            ('Copy current session as context', 'copy', '', lambda: self.copy_session(True)),
            ('Copy current session as Markdown', 'copy', '', lambda: self.copy_session(False)),
            ('Summarise session with local LLM', 'sparkle', '', self.request_summary),
            ('Start / stop dictation', 'mic', '', self.recordClicked.emit),
            ('Rename current session', 'edit', '', self._start_rename),
            ('Export current session…', 'download', '', lambda: sid() and self.export_session(sid())),
            ('Open settings', 'settings', 'Ctrl ,', self.show_settings),
            ('Set up Claude Code (MCP)', 'terminal', '', lambda: self.show_settings('claude')),
            ('Edit project vocabulary', 'sparkle', '', lambda: self.show_settings('correction')),
            ('Change theme', 'palette', '', lambda: self.show_settings('appearance')),
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
        self.toast.reposition()
        QTimer.singleShot(0, self._elide_title)

    def closeEvent(self, event):
        # Closing the window keeps WhisperWriter running in the tray.
        event.ignore()
        self.hide()
