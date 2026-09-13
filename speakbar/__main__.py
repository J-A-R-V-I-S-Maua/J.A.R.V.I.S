import argparse
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .window import Speakbar


def main(argv=None):
    parser = argparse.ArgumentParser(description="Speakbar do J.A.R.V.I.S.")
    parser.add_argument("--demo", action="store_true", help="Demonstração sem microfone ou API")
    args = parser.parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("J.A.R.V.I.S. Speakbar")
    app.setQuitOnLastWindowClosed(False)
    window = Speakbar(demo=args.demo)
    window.show()
    QTimer.singleShot(0, window.controller.start)
    previous = signal.signal(signal.SIGINT, lambda *_: window.quit())
    # Permite ao Python tratar Ctrl+C mesmo com o loop nativo do Qt em repouso.
    heartbeat = QTimer(app)
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(200)
    try:
        return app.exec()
    finally:
        signal.signal(signal.SIGINT, previous)


if __name__ == "__main__":
    sys.exit(main())
