"""Apresentação Qt da speakbar; ícones desenhados localmente."""

import math

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QVBoxLayout, QLabel, QTextEdit, QMenu, QPushButton, QSystemTrayIcon, QWidget,
)

from .controller import DemoController, VoiceController, State


class TranscriptView(QTextEdit):
    drag_started = Signal(object)
    drag_moved = Signal(object)
    drag_finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setTabChangesFocus(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(0)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.drag_started.emit(event.globalPosition().toPoint())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.drag_moved.emit(event.globalPosition().toPoint())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.drag_finished.emit()
        super().mouseReleaseEvent(event)


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
        elif self.kind == "settings":
            painter.setPen(QPen(QColor("#111111"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for y, knob_x in ((18, 22), (25, 29), (32, 23)):
                painter.drawLine(16, y, knob_x - 3, y)
                painter.drawLine(knob_x + 3, y, 34, y)
                painter.drawEllipse(QRectF(knob_x - 2.5, y - 2.5, 5, 5))
        else:
            painter.setPen(QPen(QColor("#111111"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(17, 17, 33, 33)
            painter.drawLine(33, 17, 17, 33)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#005ea8"), 2))
            painter.drawEllipse(QRectF(3, 3, 44, 44))


class Speakbar(QWidget):
    def __init__(self, *, demo=False, controller=None):
        super().__init__()
        self.demo = demo
        self._closing = False
        self._resizing_text = False
        self.setWindowTitle("J.A.R.V.I.S. — Speakbar" + (" (demonstração)" if demo else ""))
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # Sem isso, no macOS a barra some quando outro app fica ativo.
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setWindowIcon(make_icon())
        self._drag_offset = None
        self.controller = controller or (DemoController(self) if demo else VoiceController(self))
        self._settings_dialog = None
        self.preferences = self.controller.preferences
        self.high_contrast = self.preferences.effective["high_contrast"]
        self.voice = CircleButton("voice", self)
        self.settings_button = CircleButton("settings", self)
        self.settings_button.setAccessibleName("Abrir configurações do Jarvis")
        self.settings_button.setToolTip("Configurações — diga Abra as configurações")
        self.settings_button.clicked.connect(self.open_settings)
        self.controller.settings_requested.connect(self.open_settings)
        self.close_button = CircleButton("close", self)
        self.close_button.setAccessibleName("Fechar aplicação")
        self.close_button.setToolTip("Fechar aplicação (Alt+F4)")
        self.message = TranscriptView(self)
        self.guidance = QLabel(self)
        self.guidance.setWordWrap(True)
        self.guidance.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.guidance.setAccessibleName("Orientação da interação")
        self.guidance.setStyleSheet("QLabel { color: #111111; background: transparent; }")
        font = QFont("Segoe UI")
        font.setPixelSize(18)
        self.message.setFont(font)
        self.guidance.setFont(font)
        self.message.setStyleSheet("QTextEdit { color: #111111; background: transparent; border: 0; } QTextEdit:focus { border-bottom: 1px solid #005ea8; }")
        self.message.setMinimumWidth(0)
        self.message.viewport().installEventFilter(self)
        self.message.drag_started.connect(self._start_drag)
        self.message.drag_moved.connect(self._move_drag)
        self.message.drag_finished.connect(self._end_drag)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 12, 5)
        layout.setSpacing(12)
        layout.addWidget(self.voice)
        content = QVBoxLayout()
        content.setSpacing(2)
        content.setContentsMargins(0, 0, 0, 0)
        content.addWidget(self.message)
        content.addWidget(self.guidance)
        layout.addLayout(content, 1)
        layout.addWidget(self.settings_button)
        layout.addWidget(self.close_button)
        self.setTabOrder(self.voice, self.settings_button)
        self.setTabOrder(self.settings_button, self.close_button)
        self.setTabOrder(self.close_button, self.message)
        self.voice.clicked.connect(self.controller.toggle)
        self.close_button.clicked.connect(self.close)
        self.controller.state_changed.connect(self._render_state)
        self.controller.stopped.connect(self._finish_quit)
        self.overlay = None
        if hasattr(self.controller, "markers_changed"):
            from .overlay import NumberOverlay
            self.overlay = NumberOverlay()
            self.controller.markers_changed.connect(self.overlay.show_elements)
        self._render_state(self.controller.current_event)

        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip(self.windowTitle())
        self.tray_menu = QMenu()
        self.tray_menu.addAction("Mostrar barra", self.restore)
        self.tray_menu.addAction("Configurações", self.open_settings)
        self.tray_menu.addAction("Ajuda de comandos", self.show_help)
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
        self.apply_appearance(self.preferences.effective)
        values = self.preferences.effective
        if values["remember_position"] and values["position"]:
            self.keep_on_screen(QPoint(*values["position"]))

    def open_settings(self):
        if self._closing:
            return
        if self._settings_dialog is not None:
            self._settings_dialog.show()
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        from .settings_window import SettingsWindow
        self.controller.set_settings_open(True)
        dialog = SettingsWindow(self.controller, self, demo=self.demo)
        self._settings_dialog = dialog
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.saved.connect(self.apply_appearance)
        dialog.finished.connect(self._settings_closed)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def show_help(self):
        if hasattr(self.controller, "show_help"):
            self.controller.show_help()
        else:
            self.open_settings()

    def _settings_closed(self, result):
        self._settings_dialog = None
        if not self._closing:
            self.controller.set_settings_open(False)
            if hasattr(self.controller, "_show_text"):
                self.controller._show_text("Configurações salvas. Diga Hey Jarvis para começar." if result == 1 else
                                           "Alterações descartadas. Diga Hey Jarvis para começar.")
        self._render_state(self.controller.current_event)

    def apply_appearance(self, values):
        self.high_contrast = values["high_contrast"]
        font = self.message.font()
        font.setPixelSize(values["text_size"])
        self.message.setFont(font)
        self.guidance.setFont(font)
        if self.overlay:
            self.overlay.number_size = values["number_size"]
            self.overlay.high_contrast = values["high_contrast"]
        self._fit_message()
        self.update()

    def _connect_screen(self, screen):
        screen.availableGeometryChanged.connect(lambda _: self.keep_on_screen())

    def _screen_added(self, screen):
        self._connect_screen(screen)
        self.keep_on_screen()

    def _render_state(self, event):
        presentation = getattr(self.controller, "presentation", None)
        self.guidance.setText(presentation.guidance if presentation else "")
        self.guidance.setVisible(bool(self.guidance.text()))
        self._update_message()
        self.message.setAccessibleName("Transcrição e estado do reconhecimento")
        description = " ".join((presentation.message, presentation.guidance)) if presentation else event.text
        self.message.setAccessibleDescription(description)
        self.message.setToolTip(description)
        active = event.state in (State.LISTENING, State.PROCESSING)
        if hasattr(self.controller, "screen"):
            active = active or self.controller.screen.session.active
        if hasattr(self.controller, "shortcuts"):
            active = active or self.controller.shortcuts.active
        name = "Cancelar interação" if active else "Gravar comando"
        if event.state is State.ERROR:
            name = "Tentar novamente"
        if self.demo:
            name = "Cancelar demonstração" if active else "Iniciar demonstração de voz"
        self.voice.setAccessibleName(name)
        self.voice.setToolTip(name)
        self.voice.setEnabled(event.state not in (State.PREPARING, State.CANCELLING, State.STOPPING, State.STOPPED))
        if self.controller.settings_open:
            self.voice.setEnabled(False)
        self.voice.set_listening(event.state is State.LISTENING)

    def _update_message(self):
        presentation = getattr(self.controller, "presentation", None)
        text = presentation.message if presentation else self.controller.text
        if self.message.toPlainText() != text:
            self.message.setPlainText(text)
            cursor = self.message.textCursor()
            block = cursor.blockFormat()
            block.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cursor.select(cursor.SelectionType.Document)
            cursor.mergeBlockFormat(block)
            cursor.clearSelection()
            self.message.verticalScrollBar().setValue(0)
        self._fit_message()

    def _fit_message(self):
        if self._resizing_text:
            return
        self._resizing_text = True
        try:
            bottom = self.y() + self.height()
            width = max(40, self.width() - 50 * 3 - 12 * 3 - 20)
            metrics = self.message.fontMetrics()
            text_height = metrics.boundingRect(QRect(0, 0, width, 10000),
                Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere,
                self.message.toPlainText()).height()
            lines = min(3, max(1, math.ceil(text_height / metrics.lineSpacing())))
            guidance_height = self.guidance.heightForWidth(width) + 4 if self.guidance.text() else 0
            height = max(60, lines * metrics.lineSpacing() + 16 + guidance_height)
            screen = QApplication.screenAt(self.pos() + self.rect().center()) or QApplication.primaryScreen()
            self.setFixedHeight(min(height, screen.availableGeometry().height()))
            self.message.setFixedHeight(min(lines * metrics.lineSpacing() + 4, self.height() - 10))
            area = screen.availableGeometry()
            self.move(max(area.left(), min(self.x(), area.right() + 1 - self.width())),
                      max(area.top(), min(bottom - self.height(), area.bottom() + 1 - self.height())))
        finally:
            self._resizing_text = False

    def eventFilter(self, watched, event):
        if watched is self.message.viewport() and event.type() == QEvent.Type.Resize:
            self._fit_message()
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, QColor(160, 160, 160, 238))
        gradient.setColorAt(0.5, QColor(151, 151, 151, 238))
        gradient.setColorAt(1, QColor(174, 174, 174, 238))
        if self.high_contrast:
            gradient.setColorAt(0, QColor("white"))
            gradient.setColorAt(0.5, QColor("white"))
            gradient.setColorAt(1, QColor("white"))
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
        self.setFixedWidth(min(692, area.width()))
        self._fit_message()
        self.move(max(area.left(), min(position.x(), area.right() + 1 - self.width())),
                  max(area.top(), min(position.y(), area.bottom() + 1 - self.height())))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._start_drag(event.globalPosition().toPoint())
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._move_drag(event.globalPosition().toPoint())
            event.accept()

    def mouseReleaseEvent(self, event):
        self._end_drag()
        super().mouseReleaseEvent(event)

    def _start_drag(self, position):
        self._drag_offset = position - self.pos()

    def _move_drag(self, position):
        if self._drag_offset is not None:
            self.keep_on_screen(position - self._drag_offset, QApplication.screenAt(position))

    def _end_drag(self):
        self._drag_offset = None

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
        event.ignore()
        self.quit()

    def quit(self):
        if self._closing:
            return
        self._closing = True
        if self._settings_dialog:
            self._settings_dialog.reject()
        if self.preferences.effective["remember_position"] and not self.preferences.error:
            try:
                overrides = dict(self.preferences.overrides, position=[self.x(), self.y()])
                self.preferences.save(overrides, self.preferences.shortcuts)
            except (OSError, ValueError):
                import logging
                logging.exception("Não foi possível salvar a posição da barra")
        if self.overlay:
            self.overlay.clear()
        self.close_button.setEnabled(False)
        self.controller.shutdown()

    def _finish_quit(self):
        self.voice.set_listening(False)
        self.tray.hide()
        self.hide()
        QApplication.instance().quit()
