"""Estilo compartilhado das quatro áreas de configurações."""
STYLE = """
QDialog#JarvisSettings { background: #f3f4f6; }
QWidget { font-family: 'Segoe UI'; font-size: 14px; color: #20252e; }
QLabel#settingsTitle { font-size: 26px; font-weight: 600; }
QLabel#sectionTitle { font-size: 20px; font-weight: 600; }
QLabel#editorTitle { font-size: 16px; font-weight: 600; }
QLabel#mutedText { color: #586271; }
QLabel#pausedBadge { color: #005ea8; background: #e4effb; border-radius: 14px; padding: 7px 12px; }
QListWidget#settingsNavigation { background: #e9ecf0; border: 0; border-radius: 16px; padding: 10px; outline: 0; }
QListWidget#settingsNavigation::item { padding: 12px 10px; border: 1px solid transparent; border-radius: 10px; margin-bottom: 4px; }
QListWidget#settingsNavigation::item:selected { background: white; color: #005ea8; border: 1px solid #c4d9ed; }
QListWidget#settingsNavigation::item:hover { background: #f6f8fa; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: 0; }
QWidget#editorCard { background: white; border: 1px solid #dce1e7; border-radius: 16px; }
QWidget#editorCard > QLabel { background: transparent; border: 0; }
QListWidget#shortcutList { background: white; border: 1px solid #dce1e7; border-radius: 14px; padding: 8px; outline: 0; }
QListWidget#shortcutList::item { padding: 10px 8px; border: 1px solid transparent; border-radius: 8px; margin-bottom: 3px; }
QListWidget#shortcutList::item:selected { background: #eaf3fd; color: #20252e; border: 1px solid #a9cbef; }
QListWidget#shortcutList::item:hover { background: #f4f7fb; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: white; border: 1px solid #cbd2dc; border-radius: 10px; padding: 8px 11px; min-height: 20px; selection-background-color: #005ea8; }
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover { border-color: #9eabbc; }
QComboBox QAbstractItemView { background: white; color: #20252e; selection-background-color: #eaf3fd; selection-color: #20252e; }
QTextBrowser { background: white; border: 1px solid #dce1e7; border-radius: 14px; padding: 12px; }
QPushButton { background: white; border: 1px solid #cbd2dc; border-radius: 10px; padding: 8px 12px; min-height: 20px; }
QPushButton:hover { background: #eaf1f8; border-color: #a7b9cc; }
QPushButton:pressed { background: #dae6f2; }
QPushButton#primaryAction, QPushButton#saveSettings { background: #006fcd; color: white; border: 1px solid #006fcd; }
QPushButton#primaryAction:hover, QPushButton#saveSettings:hover { background: #005ea8; }
QPushButton#deleteShortcut { color: #b82f3c; }
QPushButton#deleteShortcut:hover { background: #fff0f1; border-color: #e4a6ac; }
QPushButton:disabled { color: #9299a4; background: #eceff3; border-color: #dce1e7; }
QPushButton#deleteShortcut:disabled { color: #9299a4; }
QPushButton:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QListWidget:focus, QTextBrowser:focus { border: 2px solid #005ea8; }
QCheckBox { spacing: 8px; padding: 3px 0; }
QProgressBar { background: #e4e9f0; border: 0; border-radius: 9px; min-height: 22px; text-align: center; }
QProgressBar::chunk { background: #006fcd; border-radius: 9px; }
QLabel#settingsStatus { color: #586271; background: #e9edf2; border-radius: 9px; padding: 8px 12px; }
QLabel#settingsStatus[tone="error"] { color: #a32130; background: #ffe8ec; }
QLabel#settingsStatus[tone="success"] { color: #176445; background: #e4f3eb; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 0; }
QScrollBar::handle:vertical { background: #bdc6d1; min-height: 30px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""

HIGH_CONTRAST = """
QDialog#JarvisSettings, QListWidget#settingsNavigation, QWidget#editorCard { background: white; border: 1px solid black; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListWidget#shortcutList, QTextBrowser, QPushButton { border: 1px solid black; }
QLabel#mutedText, QLabel#settingsStatus, QLabel#settingsStatus[tone="error"], QLabel#settingsStatus[tone="success"] { color: black; }
QPushButton#primaryAction, QPushButton#saveSettings { background: #005ea8; color: white; }
"""
