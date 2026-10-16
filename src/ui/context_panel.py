"""Right-hand panel: Claude Code link, smart-dictation switches, vocabulary, engine stats."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QWidget

from claude_integration import TOOL_NAMES
from transcription import parse_vocabulary
from ui import theme
from ui.widgets import ChipRow, Toggle, hbox, vbox
from utils import ConfigManager

QUICK_TOGGLES = [
    (('claude_code', 'smart_terminal_mode'), 'Claude Code aware typing'),
    (('claude_code', 'backtick_filenames'), 'Wrap file names in backticks'),
    (('claude_code', 'suppress_enter'), 'Never press Enter in Claude Code'),
    (('post_processing', 'llm_correction', 'enabled'), 'LLM correction (local)'),
]

STAT_ROWS = ('Engine', 'GPU', 'Model', 'Avg latency', 'Avg speed')


class ContextPanel(QWidget):
    settingToggled = Signal(tuple, bool)   # config keys, new value
    openSettingsRequested = Signal(str)    # settings section key

    def __init__(self):
        super().__init__()
        self.setObjectName('ContextPanel')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(312)
        outer = vbox(parent=self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        self.lay = vbox((18, 20, 18, 20), 20, inner)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        self._build_mcp_card()
        self._build_toggles()
        self._build_vocabulary()
        self._build_stats()
        self.lay.addStretch(1)

    # ------------------------------------------------------------------ build
    def _build_mcp_card(self):
        sec = vbox(spacing=10)
        sec.addWidget(theme.label('CLAUDE CODE LINK', 'section'))
        card = QFrame()
        card.setObjectName('Card')
        cl = vbox((12, 12, 12, 12), 8, card)
        head = hbox(spacing=8)
        self.mcp_dot = theme.dot(theme.INFO)
        head.addWidget(self.mcp_dot)
        self.mcp_title = theme.label(role='strong')
        head.addWidget(self.mcp_title, 1)
        cl.addLayout(head)
        self.mcp_desc = theme.label('', 'faint', wrap=True)
        cl.addWidget(self.mcp_desc)
        cl.addWidget(ChipRow(TOOL_NAMES))
        self.mcp_btn = theme.button('Set up', None, 'terminal')
        self.mcp_btn.clicked.connect(lambda: self.openSettingsRequested.emit('claude'))
        cl.addWidget(self.mcp_btn)
        sec.addWidget(card)
        self.lay.addLayout(sec)

    def _build_toggles(self):
        sec = vbox(spacing=12)
        sec.addWidget(theme.label('SMART DICTATION', 'section'))
        self.toggles = {}
        for keys, text in QUICK_TOGGLES:
            row = hbox(spacing=12)
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            row.addWidget(lbl, 1)
            toggle = Toggle()
            toggle.setAccessibleName(text)
            toggle.toggled.connect(lambda checked, k=keys: self.settingToggled.emit(k, checked))
            row.addWidget(toggle)
            self.toggles[keys] = toggle
            sec.addLayout(row)
        self.lay.addLayout(sec)

    def _build_vocabulary(self):
        sec = vbox(spacing=10)
        head = hbox()
        head.addWidget(theme.label('PROJECT VOCABULARY', 'section'), 1)
        edit = theme.button('Edit', 'ghost')
        edit.setFixedHeight(26)
        edit.clicked.connect(lambda: self.openSettingsRequested.emit('correction'))
        head.addWidget(edit)
        sec.addLayout(head)
        self.vocab_chips = ChipRow()
        sec.addWidget(self.vocab_chips)
        sec.addWidget(theme.label('Fed to Whisper as a prompt so names land right the first time.',
                                  'faint', wrap=True))
        self.lay.addLayout(sec)

    def _build_stats(self):
        card = QFrame()
        card.setObjectName('Card')
        el = vbox((12, 12, 12, 12), 8, card)
        el.addWidget(theme.label('ENGINE', 'section'))
        self.stat_values = {}
        for key in STAT_ROWS:
            row = hbox(spacing=12)
            row.addWidget(QLabel(key))
            value = theme.label('—', 'value')
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value.setWordWrap(True)
            row.addWidget(value, 1)
            self.stat_values[key] = value
            el.addLayout(row)
        self.lay.addWidget(card)

    # ------------------------------------------------------------------ refresh
    def refresh_config(self):
        for keys, toggle in self.toggles.items():
            toggle.blockSignals(True)
            toggle.setChecked(bool(ConfigManager.get_config_value(*keys)))
            toggle.blockSignals(False)
        words = parse_vocabulary(ConfigManager.get_config_value('model_options', 'common', 'vocabulary'))
        shown = words[:12] + ([f'+ {len(words) - 12}'] if len(words) > 12 else [])
        self.vocab_chips.set_chips(shown or ['none yet'])

    def set_mcp_registered(self, registered):
        theme.set_dot_color(self.mcp_dot, theme.INFO if registered else theme.FAINT)
        self.mcp_title.setText('MCP server registered' if registered else 'MCP server not set up')
        self.mcp_desc.setText(
            'Claude Code can search and read your sessions with the whisperwriter tools.'
            if registered else
            'Register the whisperwriter MCP server so Claude Code can look back through your '
            'dictations for context.')
        self.mcp_btn.setVisible(not registered)

    def set_engine(self, status):
        """Show an EngineStatus."""
        self.stat_values['Engine'].setText(status.kind.label if status.kind else '—')
        self.stat_values['GPU'].setText(status.compute_label if status.backend else '—')
        self.stat_values['Model'].setText(status.model or '—')

    def set_speed(self, avg_latency, speed_text):
        self.stat_values['Avg latency'].setText(f'{avg_latency:.2f} s' if avg_latency else '—')
        self.stat_values['Avg speed'].setText(speed_text or '—')
