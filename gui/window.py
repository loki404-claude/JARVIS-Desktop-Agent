"""Main window: left telemetry, beech mein hologram, right chat."""
import html
import queue
import threading
import time

import psutil
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QProgressBar, QPushButton,
                               QTextBrowser, QVBoxLayout, QWidget)

from core import config, tools
from core.brain import MODES, Brain, short_error
from gui.hologram import Hologram
from voice.listener import Listener
from voice.speaker import Speaker

STYLE = """
QWidget { background:#030912; color:#cfefff; font-family:'Segoe UI'; font-size:13px; }
QFrame#panel { background:#071525; border:1px solid #11496b; border-radius:14px; }
QFrame#panel QLabel, QFrame#panel QCheckBox { background:transparent; border:none; }
QCheckBox::indicator { width:14px; height:14px; border:1px solid #35dcff; border-radius:4px; background:#05101d; }
QCheckBox::indicator:checked { background:#35ffb0; }
QLabel#title { color:#35dcff; font-size:22px; font-weight:800; letter-spacing:6px; }
QLabel#head { color:#35dcff; font-weight:700; letter-spacing:2px; }
QLabel#dim { color:#6f9ab3; font-size:12px; }
QTextBrowser { background:#05101d; border:1px solid #0e3a57; border-radius:10px; padding:6px; }
QLineEdit { background:#05101d; border:1px solid #11618c; border-radius:18px; padding:9px 16px; }
QLineEdit:focus { border:1px solid #35dcff; }
QPushButton { background:#082036; border:1px solid #11618c; border-radius:17px; padding:8px 16px; font-weight:600; }
QPushButton:hover { background:#0d3455; border-color:#35dcff; }
QPushButton:checked { background:#0b5f55; border-color:#35ffb0; color:#eafff6; }
QPushButton#stop:hover { background:#4a1420; border-color:#ff5470; }
QComboBox { background:#082036; border:1px solid #11618c; border-radius:8px; padding:5px 10px; }
QComboBox QAbstractItemView { background:#071525; selection-background-color:#0d3455; }
QProgressBar { background:#05101d; border:1px solid #0e3a57; border-radius:5px; max-height:9px; }
QProgressBar::chunk { background:#22c8f5; border-radius:4px; }
"""

NOISE_PHRASES = {"", "you", "thank you", "thanks for watching", "thank you for watching",
                 "धन्यवाद", "शुक्रिया", "सब्सक्राइब करें"}
WAKE_ALIASES = ("जार्विस", "जारविस", "जर्विस")


def normalize(text: str) -> str:
    return " ".join(text.lower().strip(" .,!?।\"'").split())


class Bridge(QObject):
    """Background threads se GUI tak message pahunchane ke signals."""
    log = Signal(str, str)
    state = Signal(str)
    answer = Signal(str, str)
    heard = Signal(str)
    mic_failed = Signal(str)
    confirm = Signal(str, object)
    reminder = Signal(str)


class JarvisWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("J.A.R.V.I.S")
        self.resize(1280, 760)
        self.setStyleSheet(STYLE)
        self.bridge = Bridge()
        self.brain = Brain()
        self.speaker = Speaker(on_error=lambda m: self.bridge.log.emit("system", m))
        self.jobs = queue.Queue()
        self.audio_jobs = queue.Queue(maxsize=2)
        self.busy = False
        self.transcribing = False
        self.closing = False
        self.awake_until = 0.0
        self.listener = Listener(self._on_audio, self.bridge.mic_failed.emit, self._mic_blocked)
        self._build()
        self._connect()
        threading.Thread(target=self._brain_worker, daemon=True).start()
        threading.Thread(target=self._stt_worker, daemon=True).start()
        self._greet()

    # ---------------- Layout ----------------
    def _panel(self, title, width=None):
        frame = QFrame(objectName="panel")
        if width:
            frame.setFixedWidth(width)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.addWidget(QLabel(title, objectName="head"))
        return frame, layout

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        top = QHBoxLayout()
        top.addWidget(QLabel("J.A.R.V.I.S", objectName="title"))
        top.addStretch()
        self.clock = QLabel(objectName="head")
        top.addWidget(self.clock)
        outer.addLayout(top)

        middle = QHBoxLayout()
        left, box = self._panel("SYSTEM", 230)
        self.meters = {}
        for key in ("CPU", "RAM", "DISK", "JARVIS CPU"):
            label, bar = QLabel(key), QProgressBar()
            bar.setTextVisible(False)
            box.addWidget(label)
            box.addWidget(bar)
            self.meters[key] = (label, bar)
        box.addSpacing(14)
        box.addWidget(QLabel("AI MODEL", objectName="head"))
        self.mode = QComboBox()
        self.mode.addItems(MODES)
        box.addWidget(self.mode)
        status = (f"Groq: {'key mili ✔' if self.brain.groq else 'key nahi ✖'}\n"
                  f"Gemini: {'key mili ✔' if self.brain.gemini else 'key nahi ✖'}\n"
                  f"Search: {'Tavily + ddgs' if config.TAVILY_API_KEY else 'sirf ddgs'}")
        box.addWidget(QLabel(status, objectName="dim"))
        self.model_label = QLabel("Last model: -", objectName="dim")
        self.model_label.setWordWrap(True)
        box.addWidget(self.model_label)
        box.addSpacing(14)
        box.addWidget(QLabel("QUICK ACTIONS", objectName="head"))
        for label, command in (("Mausam", f"{config.DEFAULT_CITY} ka mausam batao"),
                               ("PC Status", "mere PC ka system status batao"),
                               ("Screen Dekho", "meri screen dekh kar batao kya dikh raha hai"),
                               ("Mere Notes", "mere saved notes sunao")):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, c=command: self.submit(c))
            box.addWidget(button)
        box.addStretch()
        self.wake = QCheckBox(f"Sirf '{config.WAKE_WORD}' bolne par")
        self.eco = QCheckBox("ECO mode (kam CPU)")
        box.addWidget(self.wake)
        box.addWidget(self.eco)
        middle.addWidget(left)

        self.hologram = Hologram(lambda: self.listener.level)
        middle.addWidget(self.hologram, 1)

        right, chat_box = self._panel("CONVERSATION", 380)
        self.chat = QTextBrowser()
        self.chat.document().setMaximumBlockCount(600)
        chat_box.addWidget(self.chat)
        clear = QPushButton("Chat saaf karo")
        clear.clicked.connect(self._clear)
        chat_box.addWidget(clear)
        middle.addWidget(right)
        outer.addLayout(middle, 1)

        bottom = QHBoxLayout()
        self.mic_button = QPushButton("🎤 MIC OFF")
        self.mic_button.setCheckable(True)
        self.voice_button = QPushButton("🔊 VOICE ON")
        self.voice_button.setCheckable(True)
        self.voice_button.setChecked(True)
        self.stop_button = QPushButton("■ CHUP (Esc)", objectName="stop")
        self.input = QLineEdit(placeholderText="Yahan likho ya mic ON karke bolo...")
        send = QPushButton("BHEJO ➤")
        send.clicked.connect(self._send)
        for widget in (self.mic_button, self.voice_button, self.stop_button):
            bottom.addWidget(widget)
        bottom.addWidget(self.input, 1)
        bottom.addWidget(send)
        outer.addLayout(bottom)

    def _connect(self):
        b = self.bridge
        b.log.connect(self.add_message)
        b.state.connect(lambda _: self._refresh_state())
        b.answer.connect(self._on_answer)
        b.heard.connect(self._on_heard)
        b.mic_failed.connect(self._on_mic_failed)
        b.confirm.connect(self._on_confirm)
        b.reminder.connect(self._on_reminder)
        tools.confirm_callback = self._ask_permission
        tools.notify_callback = self._notify
        self.mic_button.toggled.connect(self.set_mic)
        self.voice_button.toggled.connect(self.set_voice)
        self.stop_button.clicked.connect(self.speaker.stop)
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.speaker.stop)
        self.input.returnPressed.connect(self._send)
        self.mode.currentTextChanged.connect(lambda m: setattr(self.brain, "mode", m))
        self.eco.toggled.connect(lambda on: setattr(self.hologram, "eco", on))
        self.process = psutil.Process()
        self.process.cpu_percent()
        self.stats_timer = QTimer(self, timeout=self._update_stats)
        self.stats_timer.start(2000)
        self.state_timer = QTimer(self, timeout=self._refresh_state)
        self.state_timer.start(200)
        self._update_stats()

    # ---------------- Chat ----------------
    def add_message(self, who, text):
        name, color = {"user": (config.USER_NAME, "#35ffb0"), "jarvis": ("JARVIS", "#35dcff")}.get(
            who, ("system", "#7b93a6"))
        body = html.escape(text).replace("\n", "<br>")
        self.chat.append(f"<p><b style='color:{color}'>{html.escape(name)}</b><br>{body}</p>")

    def _greet(self):
        text = f"Namaste {config.USER_NAME}! Main Jarvis. Mic ON karo ya likh kar batao, kya karna hai?"
        self.add_message("jarvis", text)
        for error in self.brain.errors:
            self.add_message("system", error)
        if not (self.brain.groq or self.brain.gemini):
            self.add_message("system", ".env file mein API key daalo, phir app dobara chalao.")
        else:
            spoken = (text if config.REPLY_SCRIPT == "hinglish" else
                      f"नमस्ते {config.USER_NAME}! मैं Jarvis. Mic on करो या लिख कर बताओ, क्या करना है?")
            QTimer.singleShot(800, lambda: self.speaker.say(spoken))

    def _clear(self):
        self.chat.clear()
        if not self.busy:
            self.brain.history.clear()

    def _send(self):
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.submit(text)

    # ---------------- Commands ----------------
    def local_command(self, text) -> bool:
        """On/off wale command turant, bina AI ke."""
        t = normalize(text)
        if t in ("chup", "chup ho jao", "stop", "ruko", "bas karo", "चुप", "रुको", "स्टॉप"):
            self.speaker.stop()
        elif t in ("mic band karo", "mic off", "mic off karo", "mic band", "माइक बंद करो", "माइक बंद"):
            self.mic_button.setChecked(False)
        elif t in ("voice band karo", "voice off", "voice off karo", "aawaz band karo",
                   "आवाज़ बंद करो", "आवाज बंद करो", "वॉइस बंद करो"):
            self.voice_button.setChecked(False)
        elif t in ("voice on", "voice on karo", "voice chalu karo", "aawaz chalu karo",
                   "आवाज़ चालू करो", "आवाज चालू करो", "वॉइस चालू करो", "वॉइस ऑन करो"):
            self.voice_button.setChecked(True)
        else:
            return False
        self.add_message("system", f"Command: {text}")
        return True

    def submit(self, text):
        if self.local_command(text):
            return
        if self.busy:
            self.add_message("system", "Pehle wala kaam chal raha hai, thoda ruko.")
            return
        self.speaker.stop()
        self.add_message("user", text)
        self.busy = True
        self._refresh_state()
        self.jobs.put(text)

    def _brain_worker(self):
        while True:
            text = self.jobs.get()
            try:
                answer, model = self.brain.ask(text)
            except Exception as exc:
                answer, model = f"Error aaya: {short_error(exc)}", "-"
            if not self.closing:
                self.bridge.answer.emit(answer, model)

    def _on_answer(self, answer, model):
        self.busy = False
        self.model_label.setText(f"Last model: {model}")
        self.add_message("jarvis", answer)
        self.speaker.say(answer)
        self._refresh_state()

    # ---------------- Mic aur voice ----------------
    def set_mic(self, on):
        self.mic_button.setText("🎤 MIC ON" if on else "🎤 MIC OFF")
        if on:
            self.listener.start()
            self.add_message("system", "Mic ON hai. Jab tak band nahi karoge, main sunta rahunga.")
        else:
            self.listener.stop()
        self._refresh_state()

    def set_voice(self, on):
        self.voice_button.setText("🔊 VOICE ON" if on else "🔇 VOICE OFF")
        self.speaker.enabled = on
        if not on:
            self.speaker.stop()

    def _mic_blocked(self):
        return self.busy or self.transcribing or self.speaker.speaking

    def _on_audio(self, wav):
        try:
            self.audio_jobs.put_nowait(wav)
        except queue.Full:
            pass

    def _stt_worker(self):
        while True:
            wav = self.audio_jobs.get()
            if not self.listener.running:
                continue
            self.transcribing = True
            try:
                text = self.brain.transcribe(wav)
                if self.listener.running and not self.closing:
                    self.bridge.heard.emit(text)
                    time.sleep(0.1)  # GUI ko busy set karne ka time
            except Exception as exc:
                if not self.closing:
                    self.bridge.log.emit("system", f"Awaz samajh nahi aayi: {short_error(exc)}")
            finally:
                self.transcribing = False

    def _on_heard(self, text):
        if not self.mic_button.isChecked() or self.busy or normalize(text) in NOISE_PHRASES:
            return
        if self.local_command(text):
            return
        if self.wake.isChecked():
            lowered, found = text.lower(), None
            for word in (config.WAKE_WORD, *WAKE_ALIASES):
                if word and word in lowered:
                    found = word
                    break
            if found:
                rest = text[lowered.index(found) + len(found):].strip(" ,.!?।")
                self.awake_until = time.monotonic() + 25
                if not rest:
                    self.add_message("jarvis", "Haan bolo!")
                    self.speaker.say("Haan bolo!" if config.REPLY_SCRIPT == "hinglish" else "हाँ बोलो!")
                    return
                text = rest
                if self.local_command(text):
                    return
            elif time.monotonic() > self.awake_until:
                return
        self.submit(text)

    def _on_mic_failed(self, message):
        self.mic_button.setChecked(False)
        self.add_message("system", message)

    # ---------------- Permission, reminder, stats ----------------
    def _ask_permission(self, message) -> bool:
        """Brain thread se call hota hai; jawab aane tak rukta hai."""
        request = {"event": threading.Event(), "ok": False, "expired": False}
        self.bridge.confirm.emit(message, request)
        if not request["event"].wait(timeout=90):
            request["expired"] = True
            return False
        return request["ok"]

    def _on_confirm(self, message, request):
        if request["expired"]:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Jarvis permission")
        box.setTextFormat(Qt.PlainText)
        if len(message) > 600:  # poora text "Show Details" mein dikhta hai
            box.setText(message[:600] + "\n... (poora text 'Show Details' mein)")
            box.setDetailedText(message)
        else:
            box.setText(message)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        QTimer.singleShot(88000, box.reject)
        self.activateWindow()
        result = box.exec()
        request["ok"] = result == QMessageBox.Yes and not request["expired"]
        request["event"].set()

    def _notify(self, text):
        if self.closing:
            return
        if text.startswith("⏰"):
            self.bridge.reminder.emit(text)
        else:
            self.bridge.log.emit("system", text)

    def _on_reminder(self, text):
        self.add_message("jarvis", text)
        self.speaker.say(text.replace("⏰", ""))

    def _refresh_state(self):
        if self.speaker.speaking:
            state = "speaking"
        elif self.busy or self.transcribing:
            state = "thinking"
        else:
            state = "listening" if self.listener.running else "idle"
        self.hologram.set_state(state)

    def _update_stats(self):
        self.clock.setText(time.strftime("%a %d %b   %H:%M"))
        values = {
            "CPU": psutil.cpu_percent(),
            "RAM": psutil.virtual_memory().percent,
            "DISK": psutil.disk_usage(str(config.BASE_DIR)).percent,
            "JARVIS CPU": self.process.cpu_percent() / (psutil.cpu_count() or 1),
        }
        for key, value in values.items():
            label, bar = self.meters[key]
            label.setText(f"{key}  {value:.0f}%")
            bar.setValue(int(value))

    def closeEvent(self, event):
        self.closing = True
        self.listener.stop()
        self.speaker.stop()
        event.accept()
