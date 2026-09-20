"""Janela descartável para aceitação nativa; não lê ou grava documentos."""
import sys
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget


class Window(QWidget):
    def closeEvent(self, event):
        if sys.argv[1] == "unsaved":
            event.ignore()
            if not hasattr(self, "dialog"):
                self.dialog = QMessageBox(QMessageBox.Icon.Question, "Documento de teste não salvo",
                    "Este documento é descartável. O teste não seleciona Salvar ou Descartar.",
                    QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel, self)
                self.dialog.show()
        else:
            event.accept()


app = QApplication([])
window = Window()
window.setWindowTitle("JARVIS — teste descartável")
window.resize(420, 160)
window.show()
sys.exit(app.exec())
