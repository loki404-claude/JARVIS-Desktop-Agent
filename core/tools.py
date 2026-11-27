"""Jarvis ke saare kaam (tools). Khatarnak kaam se pehle screen par permission maangi jaati hai."""
import io
import os
import platform
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import requests

from core import config, memory, search

IS_WINDOWS = platform.system() == "Windows"

# GUI in dono ko apne functions se badal deta hai
confirm_callback = lambda message: False  # default: mana
notify_callback = print
vision_callback = None      # (image_bytes, question) -> str

REGISTRY = {}


def tool(description, params=None, required=None, confirm=None):
    """Function ko tool banata hai. confirm = function(args) -> permission ka message."""
    def wrap(fn):
        REGISTRY[fn.__name__] = {
            "fn": fn,
            "confirm": confirm,
            "schema": {
                "name": fn.__name__,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": params or {},
                    "required": required if required is not None else list(params or {}),
                },
            },
        }
        return fn
    return wrap


def S(desc, enum=None):
    schema = {"type": "string", "description": desc}
    if enum:
        schema["enum"] = enum
    return schema


def schemas() -> list:
    return [t["schema"] for t in REGISTRY.values()]


def run(name: str, args: dict) -> str:
    entry = REGISTRY.get(name)
    if not entry:
        return f"ERROR: '{name}' naam ka koi tool nahi hai."
    try:
        args = dict(args or {})
        if entry["confirm"] and not confirm_callback(entry["confirm"](args)):
            return "CANCELLED: user ne permission nahi di. Kaam nahi hua."
        notify_callback(f"⚙ {name}")
        return str(entry["fn"](**args))[:6000]
    except Exception as exc:  # tool ki galti se app band na ho
        return f"ERROR: {type(exc).__name__}: {exc}"


def _keyboard():
    try:
        import pyautogui
    except Exception as exc:
        raise RuntimeError(f"pyautogui nahi chal raha: {exc}")
    return pyautogui


def _folder(name: str) -> Path:
    key = name.strip().lower()
    names = {"desktop": "Desktop", "downloads": "Downloads", "documents": "Documents",
             "pictures": "Pictures", "music": "Music", "videos": "Videos"}
    if key not in names:
        raise ValueError("Sirf desktop, downloads, documents, pictures, music, videos allowed hain.")
    path = Path.home() / names[key]
    # Windows mein kabhi folders OneDrive ke andar hote hain
    onedrive = os.getenv("OneDrive")
    if onedrive and (Path(onedrive) / names[key]).exists():
        path = Path(onedrive) / names[key]
    return path


# ---------------- Apps aur web ----------------
APPS = {
    "notepad": "notepad.exe", "calculator": "calc.exe", "paint": "mspaint.exe",
    "file explorer": "explorer.exe", "explorer": "explorer.exe",
    "task manager": "taskmgr.exe", "settings": "ms-settings:", "camera": "microsoft.windows.camera:",
    "chrome": "chrome.exe", "edge": "msedge.exe", "word": "winword.exe",
    "excel": "excel.exe", "powerpoint": "powerpnt.exe",
}


@tool("Computer par koi installed app kholo (notepad, chrome, calculator, vs code, spotify...).",
      {"name": S("App ka naam")})
def open_app(name: str):
    key = name.strip().lower()
    if not key:
        return "ERROR: app ka naam khali hai."
    if not IS_WINDOWS:
        cmd = ["open", "-a", name] if sys.platform == "darwin" else [key.replace(" ", "-")]
        subprocess.Popen(cmd)
        return f"{name} kholne ki command bhej di."
    if key in APPS:
        try:
            os.startfile(APPS[key])
            return f"{name} khol diya."
        except OSError:
            pass
    # Start Menu ke shortcuts mein dhoondo
    roots = [Path(os.getenv(v, "")) / "Microsoft/Windows/Start Menu/Programs"
             for v in ("APPDATA", "PROGRAMDATA") if os.getenv(v)]
    links = [p for r in roots if r.exists() for p in r.rglob("*.lnk")
             if "uninstall" not in p.stem.lower()]
    aliases = {"vs code": "visual studio code", "vscode": "visual studio code"}
    want = aliases.get(key, key)
    matches = sorted((p for p in links if want in p.stem.lower()), key=lambda p: len(p.stem))
    if matches:
        os.startfile(matches[0])
        return f"{matches[0].stem} khol diya."
    return f"ERROR: '{name}' app nahi mila."


@tool("Browser mein koi website kholo.", {"url": S("Website address, jaise github.com")})
def open_website(url: str):
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    if urlparse(url).scheme not in ("http", "https"):
        return "ERROR: sirf http/https website khol sakta hoon."
    webbrowser.open(url)
    return f"{url} khol diya."


@tool("Browser mein Google ya YouTube search page kholo (gaana/video ke liye youtube).",
      {"query": S("Kya search karna hai"), "site": S("Kahan", ["google", "youtube"])})
def browser_search(query: str, site: str = "google"):
    base = ("https://www.youtube.com/results?search_query=" if site == "youtube"
            else "https://www.google.com/search?q=")
    webbrowser.open(base + quote_plus(query))
    return f"{site} par '{query}' ka search page khol diya (video khud chalani hogi)."


@tool("Internet se taaza jaankari (news, score, price) laakar padho. Khabron ke liye topic=news.",
      {"query": S("Chhota aur saaf search sawal"), "topic": S("Type", ["general", "news"])},
      required=["query"])
def web_search(query: str, topic: str = "general"):
    return search.search(query, topic, notify=notify_callback)


@tool("Kisi sheher ka abhi ka mausam.", {"city": S("Sheher ka naam")}, required=[])
def get_weather(city: str = ""):
    city = city or config.DEFAULT_CITY
    geo = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                       params={"name": city, "count": 1}, timeout=10).json()
    if not geo.get("results"):
        return f"ERROR: '{city}' nahi mila."
    place = geo["results"][0]
    now = requests.get("https://api.open-meteo.com/v1/forecast", timeout=10, params={
        "latitude": place["latitude"], "longitude": place["longitude"],
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,precipitation",
    }).json()["current"]
    return (f"{place['name']}: {now['temperature_2m']}°C (feels {now['apparent_temperature']}°C), "
            f"humidity {now['relative_humidity_2m']}%, hawa {now['wind_speed_10m']} km/h, "
            f"baarish {now['precipitation']} mm")


# ---------------- System ----------------
@tool("Abhi ka time aur date.")
def get_time():
    return datetime.now().strftime("%A, %d %B %Y, %I:%M %p")


@tool("CPU, RAM, disk aur battery ki halat.")
def system_info():
    import psutil
    disk = psutil.disk_usage(str(Path.home()))
    text = (f"CPU {psutil.cpu_percent(interval=0.4)}%, RAM {psutil.virtual_memory().percent}%, "
            f"Disk {disk.percent}% bhari, {disk.free / 1e9:.1f} GB khali")
    battery = psutil.sensors_battery()
    if battery:
        text += f", Battery {battery.percent:.0f}% ({'charging' if battery.power_plugged else 'on battery'})"
    return text


@tool("Volume badhao, ghatao ya mute/unmute karo.",
      {"action": S("Action", ["up", "down", "mute"])})
def volume(action: str):
    key = {"up": "volumeup", "down": "volumedown", "mute": "volumemute"}[action]
    _keyboard().press(key, presses=1 if action == "mute" else 5)
    return f"Volume {action} key daba di."


@tool("Music/video control.", {"action": S("Action", ["play_pause", "next", "previous"])})
def media_control(action: str):
    key = {"play_pause": "playpause", "next": "nexttrack", "previous": "prevtrack"}[action]
    _keyboard().press(key)
    return f"Media key '{action}' daba di."


@tool("Saari windows minimize karke desktop dikhao (dobara chalane par wapas).")
def show_desktop():
    if not IS_WINDOWS:
        return "ERROR: ye sirf Windows par chalta hai."
    _keyboard().hotkey("win", "d")
    return "Desktop toggle kar diya."


# ---------------- Screen ----------------
def _grab():
    from PIL import ImageGrab
    return ImageGrab.grab()


@tool("Screenshot lekar Pictures/Jarvis folder mein save karo.")
def take_screenshot():
    folder = _folder("pictures") / "Jarvis"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"shot_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
    _grab().save(path)
    return f"Screenshot save hua: {path}"


@tool("Screen ko dekhkar sawal ka jawab do (error samjhana, screen par kya hai, etc).",
      {"question": S("Screen ke baare mein sawal")},
      confirm=lambda a: "Screen ki photo AI (Gemini/Groq) ko bheji jayegi. Allow karein?")
def analyze_screen(question: str):
    if not vision_callback:
        return "ERROR: vision ready nahi hai."
    image = _grab().convert("RGB")
    image.thumbnail((1600, 1600))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=80)
    return vision_callback(buffer.getvalue(), question)


# ---------------- Keyboard aur clipboard ----------------
@tool("Jis window mein cursor hai wahan text type (paste) karo.", {"text": S("Likhne wala text")},
      confirm=lambda a: "Ye text type karun? OK ke baad 4 second mein apni window par click karein.\n\n"
                        + str(a.get("text", "")))
def type_text(text: str):
    import pyperclip
    keyboard = _keyboard()
    pyperclip.copy(text)
    time.sleep(4)
    keyboard.hotkey("command" if sys.platform == "darwin" else "ctrl", "v")
    return "Text paste kar diya (clipboard mein bhi hai)."


@tool("Keyboard shortcut dabao, jaise ctrl+s ya alt+tab.", {"keys": S("'+' se jude keys")},
      confirm=lambda a: f"Shortcut '{a.get('keys')}' dabaun? OK ke baad 4 second mein window chunein.")
def press_hotkey(keys: str):
    keyboard = _keyboard()
    parts = [k.strip().lower() for k in keys.split("+") if k.strip()]
    if not parts or any(k not in keyboard.KEYBOARD_KEYS for k in parts):
        return "ERROR: galat key naam."
    time.sleep(4)
    keyboard.hotkey(*parts)
    return f"{keys} daba diya."


@tool("Text clipboard mein copy karo.", {"text": S("Text")})
def copy_to_clipboard(text: str):
    import pyperclip
    pyperclip.copy(text)
    return "Clipboard mein copy ho gaya."


@tool("Clipboard ka text padho (translate/summary ke liye).",
      confirm=lambda a: "Clipboard ka text AI ko bheja jayega. Allow karein?")
def read_clipboard():
    import pyperclip
    return pyperclip.paste() or "Clipboard khali hai."


# ---------------- Files ----------------
@tool("Desktop/Downloads/Documents etc. ki files ki list.", {"folder": S("Folder ka naam")})
def list_files(folder: str):
    path = _folder(folder)
    files = sorted(path.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:40]
    return "\n".join(p.name for p in files) or "Folder khali hai."


SAFE_EXTENSIONS = {".txt", ".md", ".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".png", ".jpg",
                   ".jpeg", ".gif", ".webp", ".mp3", ".wav", ".mp4", ".mkv"}


@tool("Kisi folder ki document/photo/video file kholo.",
      {"folder": S("Folder ka naam"), "filename": S("Poora file naam")})
def open_file(folder: str, filename: str):
    path = _folder(folder) / Path(filename).name
    if path.suffix.lower() not in SAFE_EXTENSIONS:
        return "ERROR: suraksha ke liye ye file type nahi khol sakta."
    if not path.is_file():
        return "ERROR: file nahi mili."
    if IS_WINDOWS:
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
    return f"{path.name} khol di."


@tool("Documents/Jarvis folder mein nayi text file banao (letter, code, list).",
      {"filename": S("Naam, jaise todo.txt"), "content": S("File ka content")},
      confirm=lambda a: f"Documents/Jarvis mein '{a.get('filename')}' file banaun?")
def create_text_file(filename: str, content: str):
    name = Path(filename.replace("\\", "/")).name
    if Path(name).suffix.lower() not in {".txt", ".md", ".csv", ".py", ".html", ".json"}:
        name += ".txt"
    folder = _folder("documents") / "Jarvis"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if path.exists():
        return "ERROR: is naam ki file pehle se hai. Doosra naam do."
    path.write_text(content, encoding="utf-8")
    return f"File ban gayi: {path}"


# ---------------- Notes, yaadein, timer ----------------
@tool("Note save karo.", {"text": S("Note")})
def add_note(text: str):
    memory.add("notes", text)
    return "Note save ho gaya."


@tool("Saved notes padho.")
def read_notes():
    return "\n".join(f"{n['time']}: {n['text']}" for n in memory.items("notes")[-20:]) or "Koi note nahi hai."


@tool("User ke baare mein koi baat hamesha ke liye yaad rakho, jab user kahe 'yaad rakhna'.",
      {"fact": S("Baat")})
def remember(fact: str):
    memory.add("facts", fact)
    return "Yaad rakh liya."


@tool("Timer/reminder lagao (sirf jab tak Jarvis chal raha hai).",
      {"seconds": {"type": "integer", "description": "Kitne second baad"}, "message": S("Reminder")})
def set_timer(seconds: int, message: str):
    seconds = int(seconds)
    if not 1 <= seconds <= 86400:
        return "ERROR: 1 second se 24 ghante ke beech rakho."
    timer = threading.Timer(seconds, lambda: notify_callback(f"⏰ Reminder: {message}"))
    timer.daemon = True
    timer.start()
    return f"{seconds} second ka timer lag gaya."


# ---------------- Power ----------------
@tool("PC lock, sleep, shutdown, restart, ya pending shutdown cancel karo (Windows).",
      {"action": S("Action", ["lock", "sleep", "shutdown", "restart", "cancel"])},
      confirm=lambda a: f"PC par '{a.get('action')}' karun?")
def power_action(action: str):
    if not IS_WINDOWS:
        return "ERROR: ye sirf Windows par chalta hai."
    commands = {
        "lock": ["rundll32.exe", "user32.dll,LockWorkStation"],
        "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        "shutdown": ["shutdown", "/s", "/t", "30"],
        "restart": ["shutdown", "/r", "/t", "30"],
        "cancel": ["shutdown", "/a"],
    }
    result = subprocess.run(commands[action], capture_output=True, text=True)
    if result.returncode and action not in ("lock", "sleep"):
        return f"ERROR: {result.stderr.strip()}"
    return f"{action} command chal gayi."
