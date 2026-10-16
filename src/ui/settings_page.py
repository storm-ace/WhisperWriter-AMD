"""
Settings page (shown inside the main window).

Fields are generated from config_schema.yaml and grouped into sections. Saving applies
the changes in-process — engines and servers are restarted only when their settings
changed — so there is no app restart.
"""

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

import claude_integration
from sessions import default_db_path
from ui import theme
from ui.widgets import Toggle
from utils import ConfigManager

LABELS = {
    'language': 'Language', 'temperature': 'Temperature', 'initial_prompt': 'Initial prompt',
    'vocabulary': 'Project vocabulary', 'model': 'Model', 'device': 'Device',
    'compute_type': 'Compute type', 'condition_on_previous_text': 'Condition on previous text',
    'vad_filter': 'VAD filter', 'model_path': 'Model file', 'binary_path': 'Server binary',
    'lib_path': 'Extra DLL folder', 'host': 'Host', 'port': 'Port', 'n_threads': 'CPU threads',
    'auto_start': 'Start server automatically', 'activation_key': 'Dictate hotkey',
    'activation_key_no_enter': 'Dictate without Enter', 'input_backend': 'Input backend',
    'recording_mode': 'Recording mode', 'sound_device': 'Microphone', 'sample_rate': 'Sample rate',
    'silence_duration': 'Silence before stop (ms)', 'min_duration': 'Minimum length (ms)',
    'writing_key_press_delay': 'Key press delay (s)', 'remove_trailing_period': 'Remove trailing period',
    'add_trailing_space': 'Add trailing space', 'remove_capitalization': 'Lowercase everything',
    'input_method': 'Insert text with', 'press_enter': 'Press Enter after dictating',
    'restore_clipboard': 'Restore clipboard after paste', 'word_replacements': 'Word replacements',
    'enabled': 'Enabled', 'n_gpu_layers': 'GPU layers', 'context_size': 'Context size',
    'system_prompt': 'System prompt', 'timeout_s': 'Timeout (s)',
    'print_to_terminal': 'Log to terminal', 'hide_status_window': 'Hide floating recorder',
    'noise_on_completion': 'Beep when done', 'start_minimized': 'Start in tray',
    'new_session_after_minutes': 'New session after idle (min)', 'db_path': 'Database file',
    'smart_terminal_mode': 'Claude Code aware typing', 'suppress_enter': 'Never press Enter in Claude Code',
    'backtick_filenames': 'Wrap file names in backticks',
    'min_speech_duration': 'Minimum speech (ms)', 'vad_aggressiveness': 'Noise rejection (0–3)',
    'filter_hallucinations': 'Filter Whisper hallucinations',
}

MULTILINE = {'vocabulary', 'initial_prompt', 'word_replacements', 'system_prompt'}

E = 'model_options'
SECTIONS = [
    ('engine', 'Engine & GPU', 'chip',
     'Pick where Whisper runs. AMD (and Intel) GPUs use whisper.cpp over Vulkan; NVIDIA can use either engine.',
     [(E, 'common', 'language'), (E, 'common', 'temperature'),
      ('#whispercpp', 'whisper.cpp server'),
      (E, 'whispercpp', 'binary_path'), (E, 'whispercpp', 'model_path'),
      (E, 'whispercpp', 'lib_path'), (E, 'whispercpp', 'auto_start'), (E, 'whispercpp', 'host'),
      (E, 'whispercpp', 'port'), (E, 'whispercpp', 'n_threads'),
      ('#local', 'faster-whisper'),
      (E, 'local', 'model'), (E, 'local', 'device'), (E, 'local', 'compute_type'),
      (E, 'local', 'model_path'), (E, 'local', 'vad_filter'),
      (E, 'local', 'condition_on_previous_text')]),
    ('hotkeys', 'Hotkeys & recording', 'keyboard',
     'Shortcuts accept key combos joined with + (ctrl+shift+space) or a mouse button '
     '(mouse_back, mouse_forward, mouse_middle).',
     [('recording_options', k) for k in ('activation_key', 'activation_key_no_enter', 'recording_mode',
                                         'sound_device', 'silence_duration', 'min_speech_duration',
                                         'vad_aggressiveness', 'min_duration',
                                         'sample_rate', 'input_backend')]),
    ('output', 'Output & typing', 'type', 'How the text lands in the focused window.',
     [('post_processing', k) for k in ('input_method', 'press_enter', 'restore_clipboard',
                                       'add_trailing_space', 'remove_trailing_period',
                                       'remove_capitalization', 'writing_key_press_delay')]
     + [('misc', k) for k in ('hide_status_window', 'noise_on_completion', 'start_minimized',
                              'print_to_terminal')]),
    ('correction', 'Correction & vocabulary', 'sparkle',
     'Fix names and jargon before they reach the screen.',
     [(E, 'common', 'vocabulary'), ('post_processing', 'filter_hallucinations'),
      ('post_processing', 'word_replacements'),
      (E, 'common', 'initial_prompt'),
      ('#llm', 'Local LLM correction (llama.cpp + Vulkan)')]
     + [('post_processing', 'llm_correction', k) for k in (
         'enabled', 'binary_path', 'model_path', 'lib_path', 'auto_start', 'n_gpu_layers',
         'context_size', 'temperature', 'timeout_s', 'host', 'port', 'n_threads', 'system_prompt')]),
    ('claude', 'Claude Code', 'terminal',
     'Dictate prompts straight into Claude Code and let it read your past sessions.',
     [('claude_code', k) for k in ('smart_terminal_mode', 'suppress_enter', 'backtick_filenames')]),
    ('sessions', 'Sessions & storage', 'db',
     'Every dictation is stored locally in SQLite. Nothing leaves your machine.',
     [('sessions', k) for k in ('enabled', 'new_session_after_minutes', 'db_path')]),
]


def schema_meta(keys):
    node = ConfigManager.get_schema()
    for k in keys:
        node = node[k]
    return node


class SettingsPage(QWidget):
    saved = Signal(dict)          # previous config snapshot
    closed = Signal()
    mcpSetupRequested = Signal()
    restartEngineRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fields = {}          # keys tuple -> (widget, meta)
        self.rows = {}            # keys tuple -> row widget
        self.group_rows = {'whispercpp': [], 'local': []}
        self.section_widgets = {}
        self.nav_buttons = {}
        self.dirty = False
        self._loading = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_nav())

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setObjectName('SettingsBody')
        self.scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName('SettingsBody')
        self.body_lay = QVBoxLayout(body)
        self.body_lay.setContentsMargins(36, 28, 36, 48)
        self.body_lay.setSpacing(20)
        for section in SECTIONS:
            self._build_section(*section)
        self.body_lay.addStretch(1)
        self.scroll.setWidget(body)
        self.scroll.verticalScrollBar().valueChanged.connect(self._sync_nav)
        right.addWidget(self.scroll, 1)
        right.addWidget(self._build_save_bar())
        lay.addLayout(right, 1)

        self.load_from_config()

    # ------------------------------------------------------------------ layout
    def _build_nav(self):
        nav = QWidget()
        nav.setObjectName('SettingsNav')
        nav.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        nav.setFixedWidth(236)
        lay = QVBoxLayout(nav)
        lay.setContentsMargins(12, 16, 12, 16)
        lay.setSpacing(2)
        back = theme.button('Back to sessions', 'nav', 'back')
        back.clicked.connect(self.request_close)
        lay.addWidget(back)
        lay.addSpacing(12)
        group = QButtonGroup(self)
        for key, title, icon_name, _, _ in SECTIONS:
            b = theme.button(title.replace('&', '&&'), 'nav', icon_name)
            b.setIcon(theme.icon(icon_name, theme.MUTED))
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self.scroll_to(k))
            group.addButton(b)
            self.nav_buttons[key] = b
            lay.addWidget(b)
        lay.addStretch(1)
        return nav

    def _build_save_bar(self):
        bar = QWidget()
        bar.setObjectName('SaveBar')
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(36, 10, 24, 10)
        self.save_hint = theme.label('Changes apply immediately — no restart.', 'faint')
        lay.addWidget(self.save_hint, 1)
        self.discard_btn = theme.button('Discard')
        self.discard_btn.clicked.connect(self.load_from_config)
        lay.addWidget(self.discard_btn)
        self.save_btn = theme.button('Save && apply', 'primary', 'check')
        self.save_btn.clicked.connect(self.save)
        lay.addWidget(self.save_btn)
        return bar

    def _build_section(self, key, title, _icon, intro, fields):
        wrap = QWidget()
        wrap.setMaximumWidth(860)
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(12)
        lay.addWidget(theme.label(title, 'h1'))
        lay.addWidget(theme.label(intro, 'muted', wrap=True))

        card = QFrame()
        card.setObjectName('Card')
        cl = QVBoxLayout(card)
        cl.setContentsMargins(20, 18, 20, 18)
        cl.setSpacing(16)

        if key == 'engine':
            cl.addWidget(self._build_engine_picker())
            cl.addWidget(self._build_engine_info())
        if key == 'claude':
            cl.addWidget(self._build_mcp_box())

        group = None
        for spec in fields:
            if spec[0].startswith('#'):
                group = spec[0][1:]
                sub = theme.label(spec[1], 'h2')
                sub.setContentsMargins(0, 8, 0, 0)
                cl.addWidget(sub)
                if group in self.group_rows:
                    self.group_rows[group].append(sub)
                continue
            row = self._build_field(spec)
            if row is None:
                continue
            cl.addWidget(row)
            if group in self.group_rows:
                self.group_rows[group].append(row)

        if key == 'sessions':
            open_btn = theme.button('Open data folder', None, 'folder')
            open_btn.clicked.connect(self._open_data_folder)
            row = QHBoxLayout()
            row.addWidget(open_btn)
            row.addStretch(1)
            cl.addLayout(row)

        lay.addWidget(card)
        self.section_widgets[key] = wrap
        self.body_lay.addWidget(wrap)

    def _build_engine_picker(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.engine_group = QButtonGroup(self)
        self.engine_buttons = {}
        for value, title, desc in [
            ('whispercpp', 'whisper.cpp · Vulkan',
             'GPU on AMD, Intel and NVIDIA. Model stays loaded. Recommended for AMD.'),
            ('faster-whisper', 'faster-whisper',
             'CPU (or CUDA on NVIDIA). Works out of the box, no extra build needed.'),
        ]:
            b = QPushButton()
            b.setProperty('role', 'engine')
            bl = QVBoxLayout(b)
            bl.setContentsMargins(14, 12, 14, 12)
            bl.setSpacing(4)
            t = QLabel(title)
            t.setStyleSheet('font-weight: 600; font-size: 14px; background: transparent;')
            d = theme.label(desc, 'faint', wrap=True)
            d.setStyleSheet('background: transparent;')
            for lbl in (t, d):
                lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                bl.addWidget(lbl)
            b.setMinimumHeight(80)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAccessibleName(title)
            b.clicked.connect(lambda _=False, v=value: self._on_engine_picked(v))
            self.engine_group.addButton(b)
            self.engine_buttons[value] = b
            lay.addWidget(b, 1)
        return w

    def _build_engine_info(self):
        box = QFrame()
        box.setObjectName('InfoBox')
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.engine_dot = theme.dot(theme.INFO)
        top.addWidget(self.engine_dot)
        self.engine_info = QLabel('Engine starting…')
        self.engine_info.setStyleSheet('font-weight: 500;')
        top.addWidget(self.engine_info, 1)
        restart = theme.button('Restart engine')
        restart.setFixedHeight(30)
        restart.clicked.connect(self.restartEngineRequested)
        top.addWidget(restart)
        lay.addLayout(top)
        self.engine_detail = theme.label('', 'mono', wrap=True)
        self.engine_detail.setStyleSheet('color: #8EA9C6;')
        lay.addWidget(self.engine_detail)
        return box

    def _build_mcp_box(self):
        box = QFrame()
        box.setObjectName('InfoBox')
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        head = QHBoxLayout()
        self.mcp_state = QLabel()
        self.mcp_state.setStyleSheet('font-weight: 500;')
        head.addWidget(self.mcp_state, 1)
        lay.addLayout(head)
        lay.addWidget(theme.label(
            'The MCP server gives Claude Code read-only tools to search and read your dictation '
            'history, so it can recover context you dictated earlier.', 'faint', wrap=True))
        self.mcp_cmd = QLineEdit()
        self.mcp_cmd.setReadOnly(True)
        self.mcp_cmd.setProperty('role', 'mono')
        lay.addWidget(self.mcp_cmd)
        row = QHBoxLayout()
        copy = theme.button('Copy command', None, 'copy')
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.mcp_cmd.text()))
        row.addWidget(copy)
        add = theme.button('Add to Claude Code', 'warm', 'terminal', icon_color='#FFD3BF')
        add.clicked.connect(self.mcpSetupRequested)
        row.addWidget(add)
        row.addStretch(1)
        lay.addLayout(row)
        self.mcp_msg = theme.label('', 'faint', wrap=True)
        self.mcp_msg.hide()
        lay.addWidget(self.mcp_msg)
        return box

    def _build_field(self, keys):
        try:
            meta = schema_meta(keys)
        except KeyError:
            return None
        name = keys[-1]
        row = QWidget()
        multiline = name in MULTILINE
        lay = QVBoxLayout(row) if multiline else QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8 if multiline else 16)

        text = QVBoxLayout()
        text.setSpacing(3)
        title = QLabel(LABELS.get(name, name.replace('_', ' ').capitalize()))
        title.setStyleSheet('font-weight: 500; font-size: 14px;')
        text.addWidget(title)
        if meta.get('description'):
            desc = theme.label(meta['description'], 'faint', wrap=True)
            text.addWidget(desc)
        lay.addLayout(text, 1)

        widget, container = self._make_input(keys, meta)
        widget.setAccessibleName(title.text())
        if multiline:
            lay.addWidget(container)
        else:
            lay.addWidget(container, 0, Qt.AlignmentFlag.AlignVCenter)
        self.fields[keys] = (widget, meta)
        self.rows[keys] = row
        return row

    def _make_input(self, keys, meta):
        name, kind = keys[-1], meta.get('type')
        if name == 'sound_device':
            w = QComboBox()
            w.setFixedWidth(320)
            w.addItem('Default (system microphone)', None)
            try:
                import sounddevice as sd
                seen = set()
                for dev in sd.query_devices():
                    if dev.get('max_input_channels', 0) > 0 and dev['name'] not in seen:
                        seen.add(dev['name'])
                        w.addItem(dev['name'], dev['name'])
            except Exception:
                pass
            w.currentIndexChanged.connect(self._mark_dirty)
            return w, w
        if name in MULTILINE:
            w = QPlainTextEdit()
            w.setFixedHeight(96)
            w.setPlaceholderText({
                'vocabulary': 'auth_guard.py, JWT, OAuth, Vulkan, Stormace',
                'word_replacements': 'cloud code = Claude Code\nnitea = Nitea',
            }.get(name, ''))
            w.textChanged.connect(self._mark_dirty)
            return w, w
        if kind == 'bool':
            w = Toggle()
            w.toggled.connect(self._mark_dirty)
            return w, w
        if 'options' in meta:
            w = QComboBox()
            w.addItems([str(o) for o in meta['options']])
            w.setFixedWidth(320)
            w.currentIndexChanged.connect(self._mark_dirty)
            return w, w
        w = QLineEdit()
        w.textChanged.connect(self._mark_dirty)
        if name.endswith('_path'):
            w.setProperty('role', 'mono')
            box = QWidget()
            box.setFixedWidth(420)
            bl = QHBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(8)
            bl.addWidget(w, 1)
            browse = theme.button('Browse')
            browse.clicked.connect(lambda: self._browse(w, name, keys))
            bl.addWidget(browse)
            return w, box
        w.setFixedWidth(320 if kind == 'str' else 140)
        if name == 'language':
            w.setPlaceholderText('auto-detect (e.g. en, nl)')
        return w, w

    # ------------------------------------------------------------------ values
    def load_from_config(self):
        self._loading = True
        for keys, (w, meta) in self.fields.items():
            value = ConfigManager.get_config_value(*keys)
            if isinstance(w, Toggle):
                w.setChecked(bool(value))
            elif isinstance(w, QPlainTextEdit):
                w.setPlainText('' if value is None else str(value))
            elif isinstance(w, QComboBox) and keys[-1] == 'sound_device':
                idx = w.findData(value)
                if idx < 0 and value is not None:
                    w.addItem(f'{value} (not connected)', value)
                    idx = w.count() - 1
                w.setCurrentIndex(max(0, idx))
            elif isinstance(w, QComboBox):
                w.setCurrentText('' if value is None else str(value))
            elif isinstance(w, QLineEdit):
                w.setText('' if value is None else str(value))
        engine = ConfigManager.get_config_value('model_options', 'engine') or 'faster-whisper'
        self._apply_engine(engine)
        self._loading = False
        self._set_dirty(False)
        self.refresh_mcp()

    def _value(self, w, meta, keys):
        kind = meta.get('type')
        if isinstance(w, Toggle):
            return w.isChecked()
        if isinstance(w, QPlainTextEdit):
            return w.toPlainText().strip() or ('' if meta.get('value') == '' else None)
        if isinstance(w, QComboBox) and keys[-1] == 'sound_device':
            return w.currentData()
        if isinstance(w, QComboBox):
            return w.currentText() or None
        text = w.text().strip()
        if kind in ('int', 'float'):
            try:
                return int(text) if kind == 'int' else float(text)
            except ValueError:
                return None
        return text or None

    def save(self):
        old = ConfigManager.snapshot()
        for keys, (w, meta) in self.fields.items():
            ConfigManager.set_config_value(self._value(w, meta, keys), *keys)
        ConfigManager.set_config_value(self._engine, 'model_options', 'engine')
        try:
            ConfigManager.save_config()
        except OSError as e:
            QMessageBox.warning(self, 'Could not save', str(e))
            return
        self._set_dirty(False)
        self.saved.emit(old)

    # ------------------------------------------------------------------ engine
    def _on_engine_picked(self, engine):
        self._apply_engine(engine)
        self._mark_dirty()

    def _apply_engine(self, engine):
        self._engine = engine
        for value, b in self.engine_buttons.items():
            b.setChecked(value == engine)
        for group, widgets in self.group_rows.items():
            visible = (group == 'whispercpp') == (engine == 'whispercpp')
            for w in widgets:
                w.setVisible(visible)

    def set_engine_info(self, state, title, detail=''):
        color = {'ready': theme.INFO, 'starting': '#F2C66B', 'error': theme.DANGER}.get(state, theme.FAINT)
        self.engine_dot.setStyleSheet(f'background: {color}; border-radius: 4px;')
        self.engine_info.setText(title)
        self.engine_detail.setText(detail)
        self.engine_detail.setVisible(bool(detail))

    # ------------------------------------------------------------------ claude
    def refresh_mcp(self, message=None):
        db = ConfigManager.get_config_value('sessions', 'db_path')
        self.mcp_cmd.setText(claude_integration.add_command_text(db))
        registered = claude_integration.is_registered()
        self.mcp_state.setText('● Registered in Claude Code' if registered else
                               '○ Not registered in Claude Code yet')
        self.mcp_state.setStyleSheet(f'font-weight: 500; color: {theme.INFO_TEXT if registered else theme.MUTED};')
        if message:
            self.mcp_msg.setText(message)
            self.mcp_msg.show()

    # ------------------------------------------------------------------ misc
    def _browse(self, line_edit, name, keys):
        start = os.path.dirname(line_edit.text()) if line_edit.text() else os.path.expanduser('~')
        if name == 'lib_path':
            path = QFileDialog.getExistingDirectory(self, 'Select folder', start)
        elif name == 'db_path':
            path, _ = QFileDialog.getSaveFileName(self, 'Session database', start, 'SQLite (*.db)')
        else:
            filters = {'binary_path': 'Executables (*.exe);;All files (*)'}
            if 'llm_correction' in keys and name == 'model_path':
                flt = 'GGUF models (*.gguf);;All files (*)'
            elif 'whispercpp' in keys:
                flt = 'ggml models (*.bin);;All files (*)'
            else:
                flt = filters.get(name, 'All files (*)')
            path, _ = QFileDialog.getOpenFileName(self, 'Select file', start, flt)
        if path:
            line_edit.setText(os.path.normpath(path))

    def _open_data_folder(self):
        path = ConfigManager.get_config_value('sessions', 'db_path') or default_db_path()
        folder = os.path.dirname(path)
        os.makedirs(folder, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _mark_dirty(self, *_):
        if not self._loading:
            self._set_dirty(True)

    def _set_dirty(self, dirty):
        self.dirty = dirty
        self.save_btn.setEnabled(dirty)
        self.discard_btn.setEnabled(dirty)
        self.save_hint.setText('Unsaved changes' if dirty else 'Changes apply immediately — no restart.')

    def scroll_to(self, key):
        w = self.section_widgets.get(key)
        if w:
            self.scroll.verticalScrollBar().setValue(w.y() - 8)
            self.nav_buttons[key].setChecked(True)

    def _sync_nav(self, value):
        current = None
        for key, w in self.section_widgets.items():
            if w.y() - 40 <= value:
                current = key
        if current:
            self.nav_buttons[current].setChecked(True)

    def request_close(self):
        if self.dirty:
            box = QMessageBox(self)
            box.setWindowTitle('Unsaved changes')
            box.setText('Save your changes before leaving settings?')
            save = box.addButton('Save && apply', QMessageBox.ButtonRole.AcceptRole)
            discard = box.addButton('Discard', QMessageBox.ButtonRole.DestructiveRole)
            box.addButton('Cancel', QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is save:
                self.save()
            elif box.clickedButton() is discard:
                self.load_from_config()
            else:
                return
        self.closed.emit()
