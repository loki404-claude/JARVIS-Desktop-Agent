"""Mic check: 5 second record karta hai, level aur likha hua text dikhata hai.
Dhyan: recording text banane ke liye Groq/Gemini ko bheji jaati hai."""
import numpy as np
import sounddevice as sd

from core import config
from core.brain import Brain, short_error
from voice.listener import RATE, enhance, input_device, to_wav


def main():
    print("Mics ki list (number MIC_DEVICE mein daal sakte ho):")
    for number, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0:
            print(f"  {number}: {device['name']}")
    try:
        chosen = sd.query_devices(input_device(), "input")["name"]
        print(f"\nAbhi use ho raha hai: {chosen}")
        input("Enter dabao, phir 5 second tak normal aawaz mein ek sentence bolo...")
        audio = sd.rec(5 * RATE, samplerate=RATE, channels=1, dtype="int16", device=input_device())
        sd.wait()
    except Exception as exc:
        print(f"Mic error: {exc}")
        return
    audio = audio[:, 0]
    peak = int(np.abs(audio.astype(np.int32)).max())
    percent = peak * 100 // 32767
    print(f"\nSabse tez level: {percent}%")
    if percent < 5:
        print("Aawaz bahut dheemi: sahi mic chuno ya Windows Sound settings mein mic volume badhao.")
    elif percent >= 99:
        print("Aawaz phat rahi hai (clipping): mic volume thoda kam karo.")
    wav = to_wav(enhance(audio))
    path = config.BASE_DIR / "mic_test.wav"
    path.write_bytes(wav)
    print(f"Recording save hui: {path}  (double-click karke suno)")
    try:
        print(f"Model: {config.GROQ_STT_MODEL if config.GROQ_API_KEY else 'Gemini'}")
        print("Likha gaya:", Brain().transcribe(wav) or "(kuch nahi)")
    except Exception as exc:
        print(f"Text nahi bana: {short_error(exc)}")


if __name__ == "__main__":
    main()
