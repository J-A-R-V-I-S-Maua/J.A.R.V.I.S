"""Apresentação Qt da speakbar; ícones desenhados localmente."""

import math

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMenu, QPushButton, QSystemTrayIcon, QWidget,
)

from .controller import DemoController, State


def draw_wave(painter, center, phase=0.0, animated=False):
    painter.setPen(QPen(QColor("#151515"), 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    heights = (4, 8, 13, 20, 29, 38, 29, 20, 13, 8, 4)
    for index, height in enumerate(heights):
        if animated:
            height *= 0.65 + 0.35 * math.sin(phase + index * 0.55)
        x = center.x() + (index - 5) * 3
        painter.drawLine(QPoint(round(x), round(center.y() - height / 2)),
                         QPoint(round(x), round(center.y() + height / 2)))


def make_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#cccccc"))
    painter.drawEllipse(QRectF(2, 2, 60, 60))
    draw_wave(painter, QPoint(32, 32))
    painter.end()
    return QIcon(pixmap)


class CircleButton(QPushButton):
    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.phase = 0.0
        self.listening = False
        self.setFixedSize(50, 50)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.animation = QTimer(self)
        self.animation.setInterval(33)
        self.animation.timeout.connect(self._tick)

    def set_listening(self, listening):
        self.listening = listening
        if listening:
            self.animation.start()
        else:
            self.animation.stop()
            self.phase = 0.0
        self.update()

    def _tick(self):
        self.phase += 0.18
        self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not event.isAutoRepeat():
                self.click()
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, 0, 50)
        gradient.setColorAt(0, QColor("#dddddd" if self.underMouse() else "#c6c6c6"))
        gradient.setColorAt(1, QColor("#aaaaaa" if self.isDown() else "#d0d0d0"))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor("#eeeeee"), 0.8))
        painter.drawEllipse(QRectF(1, 1, 48, 48))
        if self.kind == "voice":
            draw_wave(painter, QPoint(25, 25), self.phase, self.listening)
        else:
            painter.setPen(QPen(QColor("#111111"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(17, 17, 33, 33)
            painter.drawLine(33, 17, 17, 33)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#005ea8"), 2))
            painter.drawEllipse(QRectF(3, 3, 44, 44))


class Speakbar(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("J.A.R.V.I.S. — Speakbar (demonstração)")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowIcon(make_icon())
        self._drag_offset = None
        self.controller = DemoController(self)
        self.voice = CircleButton("voice", self)
        self.close_button = CircleButton("close", self)
        self.close_button.setAccessibleName("Fechar aplicação")
        self.close_button.setToolTip("Fechar aplicação (Alt+F4)")
        self.message = QLabel(State.IDLE.value, self)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont("Segoe UI")
        font.setPixelSize(18)
        self.message.setFont(font)
        self.message.setStyleSheet("color: #111111; background: transparent;")
        self.message.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.message.setMinimumWidth(0)
        self.message.installEventFilter(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 12, 5)
        layout.setSpacing(12)
        layout.addWidget(self.voice)
        layout.addWidget(self.message, 1)
        layout.addWidget(self.close_button)
        self.setTabOrder(self.voice, self.close_button)
        self.voice.clicked.connect(self.controller.toggle)
        self.close_button.clicked.connect(self.close)
        self.controller.state_changed.connect(self._render_state)
        self._render_state(State.IDLE)

        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("J.A.R.V.I.S. — Speakbar (demonstração)")
        self.tray_menu = QMenu()
        self.tray_menu.addAction("Mostrar barra", self.restore)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("Sair", self.quit)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self._tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

        app = QApplication.instance()
        for screen in app.screens():
            self._connect_screen(screen)
        app.screenAdded.connect(self._screen_added)
        app.screenRemoved.connect(lambda _: QTimer.singleShot(0, self.keep_on_screen))
        area = app.primaryScreen().availableGeometry()
        self.resize(min(692, area.width()), min(60, area.height()))
        self.move(area.x() + (area.width() - self.width()) // 2,
                  max(area.top(), area.bottom() + 1 - self.height() - 24))

    def _connect_screen(self, screen):
        screen.availableGeometryChanged.connect(lambda _: self.keep_on_screen())

    def _screen_added(self, screen):
        self._connect_screen(screen)
        self.keep_on_screen()

    def _render_state(self, state):
        self._update_message()
        self.message.setAccessibleName(state.value)
        self.message.setToolTip(state.value)
        active = state is not State.IDLE
        name = "Cancelar demonstração" if active else "Iniciar demonstração de voz"
        self.voice.setAccessibleName(name)
        self.voice.setToolTip(name + " (sem usar o microfone)")
        self.voice.set_listening(state is State.LISTENING and self.isVisible())

    def _update_message(self):
        self.message.setText(self.message.fontMetrics().elidedText(
            self.controller.state.value, Qt.TextElideMode.ElideRight, self.message.width()
        ))

    def eventFilter(self, watched, event):
        if watched is self.message and event.type() == QEvent.Type.Resize:
            self._update_message()
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, QColor(160, 160, 160, 238))
        gradient.setColorAt(0.5, QColor(151, 151, 151, 238))
        gradient.setColorAt(1, QColor(174, 174, 174, 238))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor(226, 226, 226, 220), 0.8))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 30, 30)

    def keep_on_screen(self, position=None, screen=None):
        position = position if isinstance(position, QPoint) else self.pos()
        screen = screen or QApplication.screenAt(position + self.rect().center())
        if screen is None:
            # Select the nearest display when dragged through a gap or unplugged.
            def distance(candidate):
                area = candidate.availableGeometry()
                x = max(area.left(), min(position.x(), area.right()))
                y = max(area.top(), min(position.y(), area.bottom()))
                return (position.x() - x) ** 2 + (position.y() - y) ** 2
            screen = min(QApplication.screens(), key=distance)
        area = screen.availableGeometry()
        self.setFixedSize(min(692, area.width()), min(60, area.height()))
        self.move(max(area.left(), min(position.x(), area.right() + 1 - self.width())),
                  max(area.top(), min(position.y(), area.bottom() + 1 - self.height())))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            cursor = event.globalPosition().toPoint()
            self.keep_on_screen(cursor - self._drag_offset, QApplication.screenAt(cursor))
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.restore()

    def restore(self):
        self.keep_on_screen()
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        self._drag_offset = None
        event.accept()
        self.quit()

    def quit(self):
        self.controller.reset()
        self.tray.hide()
        QApplication.instance().quit()
