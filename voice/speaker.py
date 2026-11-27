"""Voice reply: edge-tts (natural Hindi aawaz), internet fail ho to pyttsx3 offline."""
import asyncio
import os
import re
import tempfile
import threading
import time

from core import config


def speakable(text: str) -> str:
    text = re.sub(r"```.*?```", " Code screen par hai. ", text, flags=re.S)
    text = re.sub(r"https?://\S+", "link", text)
    text = re.sub(r"[*_#`>|]", "", text)
    return text.strip()[:900]


class Speaker:
    def __init__(self, on_error=print):
        self.enabled = True
        self.speaking = False
        self.on_error = on_error
        self._generation = 0
        self._lock = threading.Lock()

    def say(self, text: str):
        text = speakable(text)
        if not self.enabled or not text:
            return
        self._generation += 1
        self.speaking = True
        threading.Thread(target=self._run, args=(text, self._generation), daemon=True).start()

    def stop(self):
        self._generation += 1  # purani aawaz cancel
        self.speaking = False

    def _run(self, text, generation):
        with self._lock:
            try:
                if generation != self._generation:
                    return
                try:
                    self._edge(text, generation)
                except Exception as exc:
                    if generation == self._generation:
                        self.on_error(f"Online voice fail ({type(exc).__name__}), offline voice try kar raha hoon.")
                        self._offline(text)
            except Exception as exc:
                self.on_error(f"Voice error: {exc}")
            finally:
                if generation == self._generation:
                    self.speaking = False

    def _edge(self, text, generation):
        import edge_tts
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame
        handle, path = tempfile.mkstemp(suffix=".mp3")
        os.close(handle)
        try:
            asyncio.run(asyncio.wait_for(edge_tts.Communicate(text, config.TTS_VOICE).save(path), 20))
            if generation != self._generation:
                return
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy() and generation == self._generation:
                time.sleep(0.05)
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def _offline(self, text):
        import pyttsx3
        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()
