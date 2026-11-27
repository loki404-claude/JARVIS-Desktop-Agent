"""Hamesha sunne wala mic: ek baar ON karo, har bola hua sentence apne aap pakadta hai."""
import io
import queue
import threading
import time
import wave
from collections import deque

import numpy as np

from core import config

RATE = 16000
BLOCK = 480  # 30 ms


class Segmenter:
    """Aawaz ke blocks se poore sentence kaatta hai (CPU par bahut halka)."""

    def __init__(self, sensitivity: float = 1.0):
        self.sensitivity = sensitivity
        self.noise = 150.0
        self.reset()

    def reset(self):
        self.pre = deque(maxlen=20)  # ~0.6 sec pehle se, taaki pehla shabd na kate
        self.frames = []
        self.loud = self.quiet = self.voiced = 0

    def feed(self, block: np.ndarray):
        """Sentence poora hone par int16 array, warna None."""
        rms = float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))
        threshold = max(200.0, self.noise * 2.5) / self.sensitivity
        if self.frames:  # sentence ke beech ke dheeme akshar bhi pakdo
            threshold *= 0.7
        is_voice = rms > threshold
        if not self.frames:
            self.pre.append(block)
            if is_voice:
                self.loud += 1
            else:
                self.loud = 0
                self.noise = 0.95 * self.noise + 0.05 * rms
            if self.loud >= 3:
                self.frames = list(self.pre)
                self.voiced, self.quiet = self.loud, 0
            return None
        self.frames.append(block)
        if is_voice:
            self.voiced += 1
            self.quiet = 0
        else:
            self.quiet += 1
        too_long = len(self.frames) > 660  # ~20 second
        if self.quiet < 40 and not too_long:  # ~1.2 sec chuppi = sentence khatam
            return None
        audio, voiced = np.concatenate(self.frames), self.voiced
        if too_long:  # lagataar shor ho to noise level upar karo
            self.noise = max(self.noise, rms * 0.6)
        self.reset()
        return audio if voiced >= 8 else None


def enhance(audio: np.ndarray) -> np.ndarray:
    """DC offset hatao aur dheemi aawaz ko tez karo (zyada se zyada 8 guna)."""
    data = audio.astype(np.float32)
    data -= data.mean()
    peak = float(np.percentile(np.abs(data), 99.5)) if data.size else 0.0
    if peak > 0:
        data *= min(8.0, max(1.0, 26000.0 / peak))
    return np.clip(data, -32768, 32767).astype(np.int16)


def input_device():
    """MIC_DEVICE khali = default; number ya naam ka hissa bhi chalega."""
    if not config.MIC_DEVICE:
        return None
    return int(config.MIC_DEVICE) if config.MIC_DEVICE.isdigit() else config.MIC_DEVICE


def to_wav(audio: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(audio.tobytes())
    return buffer.getvalue()


class Listener:
    def __init__(self, on_audio, on_error, is_blocked):
        self.on_audio = on_audio      # (wav_bytes)
        self.on_error = on_error      # (text)
        self.is_blocked = is_blocked  # True jab Jarvis bol/soch raha ho
        self.level = 0.0
        self._stop = threading.Event()
        self._thread = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def start(self):
        if self.running:
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, args=(self._stop,), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self.level = 0.0

    def _loop(self, stop):
        blocks = queue.Queue(maxsize=200)

        def callback(data, frames, time_info, status):
            try:
                blocks.put_nowait(data[:, 0].copy())
            except queue.Full:
                pass

        segmenter = Segmenter(config.MIC_SENSITIVITY)
        resume_at = 0.0
        try:
            import sounddevice as sd
            with sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                                blocksize=BLOCK, callback=callback, device=input_device()):
                while not stop.is_set():
                    try:
                        block = blocks.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    # Jarvis apni hi aawaz na sune
                    if self.is_blocked():
                        resume_at = time.monotonic() + 0.6
                    if time.monotonic() < resume_at:
                        segmenter.reset()
                        self.level = 0.0
                        continue
                    self.level = min(1.0, float(np.abs(block.astype(np.float32)).mean()) / 3000)
                    audio = segmenter.feed(block)
                    if audio is not None and not stop.is_set():
                        self.on_audio(to_wav(enhance(audio)))
        except Exception as exc:
            stop.set()
            self.on_error(f"Mic error: {exc}")
