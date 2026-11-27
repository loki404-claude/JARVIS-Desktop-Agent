"""3D hologram core: numpy projection + QPainter. GPU/OpenGL ki zarurat nahi, CPU halka."""
import math
import time

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QLinearGradient, QPainter, QPen, QPixmap,
                           QPolygonF, QRadialGradient)
from PySide6.QtWidgets import QWidget

STATES = {  # rang, speed, label
    "idle": ((0, 205, 255), 0.45, "STANDBY"),
    "listening": ((40, 255, 170), 0.75, "SUN RAHA HOON"),
    "thinking": ((255, 176, 46), 1.9, "SOCH RAHA HOON"),
    "speaking": ((150, 120, 255), 1.0, "BOL RAHA HOON"),
}


def sphere_points(count):
    i = np.arange(count) + 0.5
    phi = np.arccos(1 - 2 * i / count)
    theta = math.pi * (1 + 5 ** 0.5) * i
    return np.stack([np.sin(phi) * np.cos(theta), np.cos(phi), np.sin(phi) * np.sin(theta)], 1)


def rotation(ax, ay, az=0.0):
    cx, sx, cy, sy, cz, sz = (math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay),
                              math.cos(az), math.sin(az))
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return rz @ rx @ ry


class Hologram(QWidget):
    def __init__(self, level_source=lambda: 0.0):
        super().__init__()
        self.setMinimumSize(340, 360)
        self.level_source = level_source
        self.state = "idle"
        self.eco = False
        self.color = np.array(STATES["idle"][0], float)
        self.angle = self.phase = self.level = 0.0
        self.last = time.monotonic()
        self.sphere = sphere_points(600)
        self.core = sphere_points(110)
        t = np.linspace(0, 2 * math.pi, 97)
        self.circle = np.stack([np.cos(t), np.zeros_like(t), np.sin(t)], 1)
        rng = np.random.default_rng(7)
        self.dust = rng.random((46, 3))  # x, y, speed
        self._background = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(50)

    def set_state(self, state):
        self.state = state

    def tick(self):
        now = time.monotonic()
        dt, self.last = min(0.1, now - self.last), now
        # Window chhupi ho to kuch draw mat karo (CPU bachao)
        if not self.isVisible() or self.window().isMinimized():
            self.timer.setInterval(500)
            return
        active = self.state != "idle"
        fps = (18 if active else 10) if self.eco else (30 if active else 20)
        self.timer.setInterval(int(1000 / fps))
        target, speed, _ = STATES[self.state]
        self.color += (np.array(target) - self.color) * min(1, dt * 6)
        self.angle += dt * speed
        self.phase += dt
        if self.state == "speaking":
            wanted = 0.45 + 0.35 * math.sin(self.phase * 13) * math.sin(self.phase * 4.3)
        elif self.state == "listening":
            wanted = self.level_source()
        else:
            wanted = 0.08 + 0.04 * math.sin(self.phase * 2)
        self.level += (wanted - self.level) * min(1, dt * 14)
        self.update()

    def resizeEvent(self, event):
        self._background = None
        super().resizeEvent(event)

    def _make_background(self):
        """Grid aur floor ek hi baar banta hai, phir cache se aata hai."""
        w, h, ratio = self.width(), self.height(), self.devicePixelRatioF()
        pix = QPixmap(int(w * ratio), int(h * ratio))
        pix.setDevicePixelRatio(ratio)
        pix.fill(QColor(3, 9, 18))
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        glow = QRadialGradient(w / 2, h * 0.43, max(w, h) * 0.6)
        glow.setColorAt(0, QColor(0, 70, 110, 90))
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(0, 0, w, h, glow)
        horizon = h * 0.70
        for i in range(-12, 13):  # perspective floor
            p.setPen(QPen(QColor(0, 150, 220, 38), 1))
            p.drawLine(QPointF(w / 2 + i * w * 0.025, horizon), QPointF(w / 2 + i * w * 0.14, h))
        for i in range(9):
            y = horizon + (h - horizon) * (i / 8) ** 1.8
            p.setPen(QPen(QColor(0, 150, 220, 18 + i * 5), 1))
            p.drawLine(QPointF(0, y), QPointF(w, y))
        p.setPen(QPen(QColor(0, 180, 255, 10), 1))
        for y in range(0, h, 4):  # scanlines
            p.drawLine(0, y, w, y)
        p.end()
        return pix

    def _project(self, points, cx, cy, radius):
        depth = 3.2 / (3.2 - points[:, 2])
        return cx + points[:, 0] * radius * depth, cy - points[:, 1] * radius * depth

    def paintEvent(self, _event):
        if self._background is None:
            self._background = self._make_background()
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._background)
        p.setRenderHint(QPainter.Antialiasing)
        r, g, b = (int(v) for v in self.color)

        def tint(alpha):
            return QColor(r, g, b, max(0, min(255, int(alpha))))

        cx, cy = w / 2, h * 0.43
        radius = min(w * 0.23, h * 0.19) * (1 + 0.07 * self.level)

        # Projector ki roshni aur base rings
        base_y = h * 0.84
        beam = QLinearGradient(0, base_y, 0, cy)
        beam.setColorAt(0, tint(55))
        beam.setColorAt(1, tint(0))
        p.setPen(Qt.NoPen)
        p.setBrush(beam)
        p.drawPolygon(QPolygonF([QPointF(cx - radius * 0.35, base_y), QPointF(cx - radius * 1.2, cy),
                                 QPointF(cx + radius * 1.2, cy), QPointF(cx + radius * 0.35, base_y)]))
        p.setBrush(Qt.NoBrush)
        for i, scale in enumerate((0.45, 0.75, 1.1)):
            p.setPen(QPen(tint(150 - i * 40), 2 - i * 0.5))
            p.drawEllipse(QPointF(cx, base_y), radius * scale, radius * scale * 0.18)

        # Core glow
        glow = QRadialGradient(cx, cy, radius * (0.75 + 0.35 * self.level))
        glow.setColorAt(0, QColor(220, 250, 255, 190))
        glow.setColorAt(0.22, tint(135))
        glow.setColorAt(1, tint(0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), radius * 1.15, radius * 1.15)
        p.setBrush(Qt.NoBrush)

        # 3D particle sphere (aawaz ke saath lehrata hai)
        wave = 1 + self.level * 0.16 * np.sin(self.sphere[:, 1] * 9 + self.phase * 9)
        pts = (self.sphere * wave[:, None]) @ rotation(0.38, self.angle).T
        self._dots(p, pts, cx, cy, radius * 1.08, tint, 2.9)
        inner = self.core @ rotation(-0.6, -self.angle * 1.7, 0.3).T
        self._dots(p, inner * (0.42 + 0.16 * self.level), cx, cy, radius, tint, 2.0)

        # Gyroscope rings: peeche wala hissa halka, aage wala tez
        for tilt_x, tilt_z, spin, size in ((1.25, 0.35, 0.7, 1.34), (0.35, -0.75, -1.1, 1.5),
                                           (1.75, 1.1, 0.5, 1.64)):
            ring = (self.circle * size) @ rotation(tilt_x, self.angle * spin, tilt_z).T
            xs, ys = self._project(ring, cx, cy, radius)
            for i in range(0, 96, 8):
                front = ring[i + 4, 2] > 0
                p.setPen(QPen(tint(190 if front else 55), 1.6 if front else 1))
                p.drawPolyline(QPolygonF([QPointF(xs[j], ys[j]) for j in range(i, i + 9)]))
            node = int((self.angle * abs(spin) * 30) % 96)
            p.setPen(QPen(QColor(235, 255, 255, 230), 6, Qt.SolidLine, Qt.RoundCap))
            p.drawPoint(QPointF(xs[node], ys[node]))

        # HUD arcs
        for i, (scale, span, speed) in enumerate(((1.82, 70, 34), (1.94, 28, -52))):
            rr = radius * scale
            rect = QRectF(cx - rr, cy - rr, rr * 2, rr * 2)
            p.setPen(QPen(tint(150 - i * 40), 2.5 - i))
            for k in range(3):
                p.drawArc(rect, int((self.angle * speed + k * 120) * 16), span * 16)
        p.setPen(QPen(tint(110), 1))
        for k in range(72):
            a = k * math.pi / 36 + self.angle * 0.08
            inner_r, outer_r = radius * 2.02, radius * (2.09 if k % 6 == 0 else 2.05)
            p.drawLine(QPointF(cx + math.cos(a) * inner_r, cy + math.sin(a) * inner_r),
                       QPointF(cx + math.cos(a) * outer_r, cy + math.sin(a) * outer_r))

        # Udte hue particles
        p.setPen(QPen(tint(110), 2, Qt.SolidLine, Qt.RoundCap))
        for x, y, s in self.dust:
            p.drawPoint(QPointF(x * w, ((y - self.phase * (0.015 + s * 0.035)) % 1.0) * h))

        # Scan beam
        scan = (self.phase * 0.22 % 1.0) * h
        grad = QLinearGradient(0, scan - 30, 0, scan)
        grad.setColorAt(0, tint(0))
        grad.setColorAt(1, tint(30))
        p.fillRect(QRectF(0, scan - 30, w, 30), grad)

        # Waveform aur status
        bars = 41
        p.setPen(QPen(tint(210), 3, Qt.SolidLine, Qt.RoundCap))
        for i in range(bars):
            shape = math.exp(-((i - bars / 2) / (bars / 4)) ** 2)
            height = 2 + 30 * self.level * shape * abs(math.sin(self.phase * 11 + i * 0.7))
            x = cx + (i - bars // 2) * 7
            p.drawLine(QPointF(x, h * 0.905 - height / 2), QPointF(x, h * 0.905 + height / 2))
        font = QFont("Consolas", 11, QFont.Bold)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 4)
        p.setFont(font)
        p.setPen(tint(240))
        p.drawText(QRectF(0, h - 32, w, 26), Qt.AlignCenter, STATES[self.state][2])
        p.end()

    def _dots(self, painter, points, cx, cy, radius, tint, size):
        xs, ys = self._project(points, cx, cy, radius)
        buckets = np.clip(((points[:, 2] + 1) * 2).astype(int), 0, 3)
        for level, alpha in enumerate((75, 130, 200, 255)):
            mask = buckets == level
            if mask.any():
                painter.setPen(QPen(tint(alpha), size * (0.6 + level * 0.18), Qt.SolidLine, Qt.RoundCap))
                painter.drawPoints(QPolygonF([QPointF(x, y) for x, y in zip(xs[mask], ys[mask])]))
