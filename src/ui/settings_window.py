import os
import sys
from PyQt5.QtWidgets import (
    QApplication, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox,
    QMessageBox, QTabWidget, QWidget, QSizePolicy, QSpacerItem, QToolButton, QStyle, QFileDialog,
    QPlainTextEdit
)
from PyQt5.QtCore import Qt, QCoreApplication, QProcess, pyqtSignal

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from ui.base_window import BaseWindow
from utils import ConfigManager

class SettingsWindow(BaseWindow):
    settings_closed = pyqtSignal()
    settings_saved = pyqtSignal()

    def __init__(self):
        """Initialize the settings window."""
        super().__init__('Settings', 700, 700)
        self.schema = ConfigManager.get_schema()
        self.init_settings_ui()

    def init_settings_ui(self):
        """Initialize the settings user interface."""
        self.tabs = QTabWidget()
        self.main_layout.addWidget(self.tabs)

        self.create_tabs()
        self.create_buttons()

        # The Engine setting drives which model settings are relevant
        # (faster-whisper -> local section, whispercpp -> whispercpp section).
        # Only the matching section is shown.
        self.engine_combo = self.findChild(QComboBox, 'model_options_engine_input')
        if self.engine_combo:
            self.engine_combo.currentTextChanged.connect(self.toggle_engine_options)
            self.toggle_engine_options(self.engine_combo.currentText())

    def create_tabs(self):
        """Create tabs for each category in the schema."""
        for category, settings in self.schema.items():
            tab = QWidget()
            tab_layout = QVBoxLayout()
            tab.setLayout(tab_layout)
            self.tabs.addTab(tab, category.replace('_', ' ').capitalize())

            self.create_settings_widgets(tab_layout, category, settings)
            tab_layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))

    def create_settings_widgets(self, layout, category, settings):
        """Create widgets for each setting in a category."""
        for sub_category, sub_settings in settings.items():
            if isinstance(sub_settings, dict) and 'value' in sub_settings:
                self.add_setting_widget(layout, sub_category, sub_settings, category)
            else:
                for key, meta in sub_settings.items():
                    self.add_setting_widget(layout, key, meta, category, sub_category)

    def create_buttons(self):
        """Create reset and save buttons."""
        reset_button = QPushButton('Reset to saved settings')
        reset_button.clicked.connect(self.reset_settings)
        self.main_layout.addWidget(reset_button)

        save_button = QPushButton('Save')
        save_button.clicked.connect(self.save_settings)
        self.main_layout.addWidget(save_button)

    def add_setting_widget(self, layout, key, meta, category, sub_category=None):
        """Add a setting widget to the layout."""
        item_layout = QHBoxLayout()
        label = QLabel(f"{key.replace('_', ' ').capitalize()}:")
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        widget = self.create_widget_for_type(key, meta, category, sub_category)
        if not widget:
            return

        help_button = self.create_help_button(meta.get('description', ''))

        item_layout.addWidget(label)
        if isinstance(widget, QWidget):
            item_layout.addWidget(widget)
        else:
            item_layout.addLayout(widget)
        item_layout.addWidget(help_button)
        layout.addLayout(item_layout)

        # Set object names for the widget, label, and help button
        widget_name = f"{category}_{sub_category}_{key}_input" if sub_category else f"{category}_{key}_input"
        label_name = f"{category}_{sub_category}_{key}_label" if sub_category else f"{category}_{key}_label"
        help_name = f"{category}_{sub_category}_{key}_help" if sub_category else f"{category}_{key}_help"
        
        label.setObjectName(label_name)
        help_button.setObjectName(help_name)
        
        if isinstance(widget, QWidget):
            widget.setObjectName(widget_name)
        else:
            # If it's a layout (for model_path), set the object name on the QLineEdit
            line_edit = widget.itemAt(0).widget()
            if isinstance(line_edit, QLineEdit):
                line_edit.setObjectName(widget_name)

    def create_widget_for_type(self, key, meta, category, sub_category):
        """Create a widget based on the meta type."""
        meta_type = meta.get('type')
        current_value = self.get_config_value(category, sub_category, key, meta)

        if key == 'sound_device':
            return self.create_device_combobox(current_value)
        if sub_category == 'whispercpp' and key == 'model_path':
            return self.create_ggml_model_combo(current_value)
        if key == 'word_replacements':
            return self.create_multiline_edit(current_value)
        if meta_type == 'bool':
            return self.create_checkbox(current_value, key)
        elif meta_type == 'str' and 'options' in meta:
            return self.create_combobox(current_value, meta['options'])
        elif meta_type == 'str':
            return self.create_line_edit(current_value, key)
        elif meta_type in ['int', 'float']:
            return self.create_line_edit(str(current_value))
        return None

    def create_checkbox(self, value, key):
        widget = QCheckBox()
        widget.setChecked(value)
        return widget

    def create_combobox(self, value, options):
        widget = QComboBox()
        widget.addItems(options)
        widget.setCurrentText(value)
        return widget

    def create_device_combobox(self, value):
        """Combobox listing available input devices for `sound_device`.

        Each item stores the device index as userData (None = system default), so the
        config keeps the numeric index while the user picks a readable microphone name.
        """
        widget = QComboBox()
        widget.setProperty('device_combo', True)
        widget.addItem('Default (system microphone)', None)
        try:
            import sounddevice as sd
            seen = set()
            for dev in sd.query_devices():
                name = dev['name']
                if dev.get('max_input_channels', 0) > 0 and name not in seen:
                    seen.add(name)
                    # Store the device name (not the index): indexes shift when devices
                    # connect/disconnect, names are stable. sounddevice accepts either.
                    widget.addItem(name, name)
        except Exception as e:
            ConfigManager.console_print(f'Could not list audio input devices: {e}')
        self._select_by_data(widget, value)
        return widget

    def create_ggml_model_combo(self, value):
        """Dropdown of ggml-*.bin models found next to the current whisper.cpp model.

        Stores the full path (userData) so picking a model is as easy as for
        faster-whisper. Drop new ggml-*.bin files in the same folder to see them here.
        """
        import glob
        widget = QComboBox()
        widget.setProperty('ggml_model_combo', True)
        search_dir = os.path.dirname(value) if value else ''
        if not search_dir or not os.path.isdir(search_dir):
            guess = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'whisper.cpp', 'models'))
            search_dir = guess if os.path.isdir(guess) else search_dir
        found = sorted(glob.glob(os.path.join(search_dir, 'ggml-*.bin'))) if search_dir and os.path.isdir(search_dir) else []
        for path in found:
            widget.addItem(os.path.basename(path), path)
        if value and value not in found:
            widget.addItem(f'{os.path.basename(value)}  (current)', value)
        if widget.count() == 0:
            widget.addItem('(no ggml-*.bin models found)', value or None)
        self._select_by_data(widget, value)
        return widget

    def _select_by_data(self, widget, value):
        """Select the combobox item whose stored userData matches `value`."""
        for i in range(widget.count()):
            if widget.itemData(i) == value:
                widget.setCurrentIndex(i)
                return
        widget.setCurrentIndex(0)  # fall back to the first item

    def create_multiline_edit(self, value):
        widget = QPlainTextEdit(value or '')
        widget.setFixedHeight(90)
        widget.setPlaceholderText('cloud code = Claude Code\nnitea = Nitea')
        return widget

    def create_line_edit(self, value, key=None):
        widget = QLineEdit(value)
        if key == 'model_path':
            layout = QHBoxLayout()
            layout.addWidget(widget)
            browse_button = QPushButton('Browse')
            browse_button.clicked.connect(lambda: self.browse_model_path(widget))
            layout.addWidget(browse_button)
            layout.setContentsMargins(0, 0, 0, 0)
            container = QWidget()
            container.setLayout(layout)
            return container
        return widget

    def create_help_button(self, description):
        help_button = QToolButton()
        help_button.setIcon(self.style().standardIcon(QStyle.SP_MessageBoxQuestion))
        help_button.setAutoRaise(True)
        help_button.setToolTip(description)
        help_button.setCursor(Qt.PointingHandCursor)
        help_button.setFocusPolicy(Qt.TabFocus)
        help_button.clicked.connect(lambda: self.show_description(description))
        return help_button

    def get_config_value(self, category, sub_category, key, meta):
        if sub_category:
            return ConfigManager.get_config_value(category, sub_category, key) or meta['value']
        return ConfigManager.get_config_value(category, key) or meta['value']

    def browse_model_path(self, widget):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Whisper Model File", "", "Model Files (*.bin);;All Files (*)")
        if file_path:
            widget.setText(file_path)

    def show_description(self, description):
        """Show a description dialog."""
        QMessageBox.information(self, 'Description', description)

    def save_settings(self):
        """Save the settings to the config file."""
        self.iterate_settings(self.save_setting)
        ConfigManager.save_config()
        QMessageBox.information(self, 'Settings Saved', 'Settings have been saved. The application will now restart.')
        self.settings_saved.emit()
        self.close()

    def save_setting(self, widget, category, sub_category, key, meta):
        value = self.get_widget_value_typed(widget, meta.get('type'))
        if sub_category:
            ConfigManager.set_config_value(value, category, sub_category, key)
        else:
            ConfigManager.set_config_value(value, category, key)

    def reset_settings(self):
        """Reset the settings to the saved values."""
        ConfigManager.reload_config()
        self.update_widgets_from_config()

    def update_widgets_from_config(self):
        """Update all widgets with values from the current configuration."""
        self.iterate_settings(self.update_widget_value)

    def update_widget_value(self, widget, category, sub_category, key, meta):
        """Update a single widget with the value from the configuration."""
        if sub_category:
            config_value = ConfigManager.get_config_value(category, sub_category, key)
        else:
            config_value = ConfigManager.get_config_value(category, key)

        self.set_widget_value(widget, config_value, meta.get('type'))

    def set_widget_value(self, widget, value, value_type):
        """Set the value of the widget."""
        if isinstance(widget, QComboBox) and (widget.property('device_combo') or widget.property('ggml_model_combo')):
            self._select_by_data(widget, value)
        elif isinstance(widget, QCheckBox):
            widget.setChecked(value)
        elif isinstance(widget, QPlainTextEdit):
            widget.setPlainText(str(value) if value is not None else '')
        elif isinstance(widget, QComboBox):
            widget.setCurrentText(value)
        elif isinstance(widget, QLineEdit):
            widget.setText(str(value) if value is not None else '')
        elif isinstance(widget, QWidget) and widget.layout():
            # This is for the model_path widget
            line_edit = widget.layout().itemAt(0).widget()
            if isinstance(line_edit, QLineEdit):
                line_edit.setText(str(value) if value is not None else '')

    def get_widget_value_typed(self, widget, value_type):
        """Get the value of the widget with proper typing."""
        if isinstance(widget, QComboBox) and (widget.property('device_combo') or widget.property('ggml_model_combo')):
            return widget.currentData()
        elif isinstance(widget, QCheckBox):
            return widget.isChecked()
        elif isinstance(widget, QPlainTextEdit):
            return widget.toPlainText() or None
        elif isinstance(widget, QComboBox):
            return widget.currentText() or None
        elif isinstance(widget, QLineEdit):
            text = widget.text()
            if value_type == 'int':
                return int(text) if text else None
            elif value_type == 'float':
                return float(text) if text else None
            else:
                return text or None
        elif isinstance(widget, QWidget) and widget.layout():
            # This is for the model_path widget
            line_edit = widget.layout().itemAt(0).widget()
            if isinstance(line_edit, QLineEdit):
                return line_edit.text() or None
        return None

    def toggle_engine_options(self, engine):
        """Show only the model settings relevant to the selected engine."""
        section_for = {'faster-whisper': 'local', 'whispercpp': 'whispercpp'}
        visible_section = section_for.get(engine)

        def apply(widget, category, sub_category, key, meta):
            if category != 'model_options':
                return
            if sub_category in ('local', 'whispercpp'):
                self._set_row_visible(widget, category, sub_category, key, sub_category == visible_section)

        self.iterate_settings(apply)

    def _set_row_visible(self, widget, category, sub_category, key, visible):
        """Show/hide a full setting row: input widget, label and help button."""
        widget.setVisible(visible)
        base = f"{category}_{sub_category}_{key}" if sub_category else f"{category}_{key}"
        label = self.findChild(QLabel, base + "_label")
        help_button = self.findChild(QToolButton, base + "_help")
        if label:
            label.setVisible(visible)
        if help_button:
            help_button.setVisible(visible)


    def iterate_settings(self, func):
        """Iterate over all settings and apply a function to each."""
        for category, settings in self.schema.items():
            for sub_category, sub_settings in settings.items():
                if isinstance(sub_settings, dict) and 'value' in sub_settings:
                    widget = self.findChild(QWidget, f"{category}_{sub_category}_input")
                    if widget:
                        func(widget, category, None, sub_category, sub_settings)
                else:
                    for key, meta in sub_settings.items():
                        widget = self.findChild(QWidget, f"{category}_{sub_category}_{key}_input")
                        if widget:
                            func(widget, category, sub_category, key, meta)

    def closeEvent(self, event):
        """Confirm before closing the settings window without saving."""
        reply = QMessageBox.question(
            self,
            'Close without saving?',
            'Are you sure you want to close without saving?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            ConfigManager.reload_config()  # Revert to last saved configuration
            self.update_widgets_from_config()
            self.settings_closed.emit()
            super().closeEvent(event)
        else:
            event.ignore()
