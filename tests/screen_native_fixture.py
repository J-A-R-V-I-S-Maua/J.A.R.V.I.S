"""Janela descartável para a verificação manual de UI Automation no Windows."""
import json
import os
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QLineEdit, QListWidget, QPushButton, QVBoxLayout, QWidget

app = QApplication([])
window = QWidget()
window.setWindowTitle("JARVIS UI Automation Test")
layout = QVBoxLayout(window)
button = QPushButton("Test action")
button.clicked.connect(lambda: print(json.dumps({"event": "invoke"}), flush=True))
button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
button.customContextMenuRequested.connect(lambda position: print(json.dumps({"event": "context_menu"}), flush=True))
layout.addWidget(button)
checkbox = QCheckBox("Test checkbox")
checkbox.toggled.connect(lambda checked: print(json.dumps({"event": "toggle", "checked": checked}), flush=True))
layout.addWidget(checkbox)
edit = QLineEdit()
edit.setAccessibleName("Test input")
edit.setText("Prefixo: ")
edit.textChanged.connect(lambda text: print(json.dumps({"event": "text", "text": text}), flush=True))
edit.returnPressed.connect(lambda: print(json.dumps({"event": "enter", "text": edit.text()}), flush=True))
layout.addWidget(edit)
items = QListWidget()
items.addItems(["First test item", "Second test item"])
items.itemSelectionChanged.connect(lambda: print(json.dumps({"event": "select", "rows": [items.row(item) for item in items.selectedItems()]}), flush=True))
layout.addWidget(items)
window.resize(420, 300)
window.show()
window.activateWindow()
QTimer.singleShot(0, lambda: print(json.dumps({"handle": int(window.winId()), "pid": os.getpid()}), flush=True))
QTimer.singleShot(20000, app.quit)
app.exec()
