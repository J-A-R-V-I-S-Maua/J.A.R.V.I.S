"""Marcadores Qt sem foco ou entrada; coordenadas físicas UIA convertidas por monitor."""
import sys
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget
from screen_access.backend import intersection


def convert_frame(frame, physical, logical):
    """Mapeia coordenadas físicas para coordenadas Qt; origens podem ser negativas."""
    x, y, width, height = frame
    px, py, pw, ph = physical
    lx, ly, lw, lh = logical
    return (lx+(x-px)*lw/pw, ly+(y-py)*lh/ph, width*lw/pw, height*lh/ph)


def physical_monitors():
    if sys.platform != "win32":
        return {}
    import ctypes
    from ctypes import wintypes

    class Info(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD), ("device", wintypes.WCHAR*32)]

    user32 = ctypes.windll.user32
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Info)]
    result = {}
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                     ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def collect(handle, dc, rect, data):
        info = Info()
        info.size = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            r = info.monitor
            result[info.device] = (r.left, r.top, r.right-r.left, r.bottom-r.top)
        return True

    callback = callback_type(collect)
    user32.EnumDisplayMonitors(None, None, callback, 0)
    return result


def monitor_for_screen(name, logical, ratio, monitors):
    if name in monitors:
        return monitors[name]
    # Qt pode devolver o nome comercial do monitor em vez de \\.\DISPLAYn.
    # No Windows, Qt preserva a origem do monitor e escala suas dimensões.
    candidates = [frame for frame in monitors.values()
                  if frame[:2] == logical[:2] and abs(frame[2]-logical[2]*ratio) <= 2
                  and abs(frame[3]-logical[3]*ratio) <= 2]
    return candidates[0] if len(candidates) == 1 else None


class MarkerWindow(QWidget):
    def __init__(self, screen, number_size=12, high_contrast=False):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus
                            | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setGeometry(screen.geometry())
        self.markers = []
        self.number_size = number_size
        self.high_contrast = high_contrast
        self.setWindowTitle("J.A.R.V.I.S. — Números")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = painter.font()
        font.setPixelSize(self.number_size)
        font.setBold(True)
        painter.setFont(font)
        occupied = []
        for number, frame in self.markers:
            x, y, width, height = frame
            outline = QColor("#111111" if self.high_contrast else "#005ea8")
            if not self.high_contrast:
                outline.setAlpha(190)
            painter.setPen(QPen(outline, 1.25))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(x, y, width, height), 3, 3)
            badge_width = max(22, painter.fontMetrics().horizontalAdvance(str(number)) + 8)
            badge_height = max(18, painter.fontMetrics().height() + 4)
            badge = QRectF(max(0, min(x, self.width()-badge_width)), max(0, min(y, self.height()-badge_height)), badge_width, badge_height)
            while any(badge.intersects(previous) for previous in occupied) and badge.bottom()+badge_height+1 < self.height():
                badge.translate(0, badge_height+1)
            occupied.append(QRectF(badge))
            painter.setPen(Qt.PenStyle.NoPen)
            background = QColor("black" if self.high_contrast else "#005ea8")
            if not self.high_contrast:
                background.setAlpha(160)
            painter.setBrush(background)
            painter.drawRoundedRect(badge, 4, 4)
            painter.setPen(QColor("white"))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(number))


class NumberOverlay:
    def __init__(self):
        self.windows = []
        self.number_size = 12
        self.high_contrast = False

    def clear(self):
        for window in self.windows:
            window.hide()
            window.deleteLater()
        self.windows = []

    def show_elements(self, elements):
        self.clear()
        if not elements:
            return
        monitors = physical_monitors()
        for screen in QApplication.screens():
            geometry = screen.geometry()
            logical = (geometry.x(), geometry.y(), geometry.width(), geometry.height())
            # AX usa pontos; UIA usa pixels físicos.
            physical = monitor_for_screen(screen.name(), logical, screen.devicePixelRatio(), monitors) if sys.platform == "win32" else logical
            if physical is None:
                continue  # Sem mapa confiável, não desenhar em uma posição incorreta.
            markers = []
            for element in elements:
                clipped = intersection(element.frame, physical)
                if clipped:
                    mapped = convert_frame(clipped, physical, logical)
                    markers.append((element.number, (mapped[0]-logical[0], mapped[1]-logical[1], mapped[2], mapped[3])))
            if markers:
                window = MarkerWindow(screen, self.number_size, self.high_contrast)
                window.markers = markers
                window.show()
                self.windows.append(window)
