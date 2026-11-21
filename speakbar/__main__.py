import argparse
import logging
import os
import shutil
import signal
import stat
import sys

from PySide6.QtCore import QLibraryInfo, QTimer
from PySide6.QtWidgets import QApplication

from .window import Speakbar


def unhide_qt_plugins(root=None):
    """Tira a marca de "oculto" dos plugins do Qt no macOS. Devolve quantos arquivos mudou.

    O Qt ignora arquivos ocultos quando procura plugins, e algo no macOS chegou a marcar como
    ocultos quase todos os arquivos da .venv. Sem o plugin "cocoa", a barra nem abre ("Could not
    find the Qt platform plugin cocoa"). A marca não muda nada nos arquivos; só some do Finder.
    """
    if sys.platform != "darwin" or not hasattr(os, "chflags"):
        return 0
    root = root or QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
    changed = 0
    for folder, folders, files in os.walk(root):
        for path in [folder] + [os.path.join(folder, name) for name in folders + files]:
            try:
                flags = os.lstat(path).st_flags
                if flags & stat.UF_HIDDEN:
                    os.chflags(path, flags & ~stat.UF_HIDDEN, follow_symlinks=False)
                    changed += 1
            except OSError:
                logging.warning("Não consegui mostrar o plugin do Qt %s", path, exc_info=True)
    return changed


def prepare_qt_plugins(source=None, cache=None):
    """No macOS, aponta o Qt para uma cópia dos plugins fora da .venv. Devolve a pasta usada.

    Com a pasta Documentos no iCloud, o macOS volta a marcar como ocultos os arquivos da .venv
    minutos depois de a marca sair; o Qt ignora plugins ocultos e a barra não abre. A cópia fica
    em ~/Library/Caches (fora do iCloud), é feita uma vez por versão do PySide6 e sobrevive a
    reinícios, ao contrário da /tmp. Um QT_PLUGIN_PATH já definido é respeitado.
    """
    if sys.platform != "darwin" or os.environ.get("QT_PLUGIN_PATH"):
        return None
    import PySide6
    source = source or QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
    cache = cache or os.path.join(os.path.expanduser("~"), "Library", "Caches", "Jarvis")
    target = os.path.join(cache, f"qt-plugins-{PySide6.__version__}")
    try:
        if not os.path.isfile(os.path.join(target, "platforms", "libqcocoa.dylib")):
            partial = target + "-parcial"  # uma cópia interrompida nunca vira a pasta usada
            shutil.rmtree(partial, ignore_errors=True)
            shutil.copytree(source, partial, symlinks=True, copy_function=shutil.copy)
            shutil.rmtree(target, ignore_errors=True)
            os.replace(partial, target)
        unhide_qt_plugins(target)
    except OSError:
        logging.warning("Não consegui copiar os plugins do Qt; usando os da .venv", exc_info=True)
        unhide_qt_plugins(source)
        return None
    os.environ["QT_PLUGIN_PATH"] = target
    return target


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Speakbar do J.A.R.V.I.S.")
    parser.add_argument("--demo", action="store_true", help="Demonstração sem microfone ou API")
    args = parser.parse_args(argv)
    prepare_qt_plugins()
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
