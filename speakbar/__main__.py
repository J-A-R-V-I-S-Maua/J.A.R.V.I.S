import sys

from PySide6.QtWidgets import QApplication

from .window import Speakbar


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("J.A.R.V.I.S. Speakbar")
    app.setQuitOnLastWindowClosed(False)
    window = Speakbar()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
