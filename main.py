"""JARVIS start karne ki file.  VS Code mein ise kholo aur F5 dabao."""
import sys


def main():
    try:
        from PySide6.QtWidgets import QApplication
        from gui.window import JarvisWindow
    except ImportError as exc:
        print(f"Library missing: {exc.name}\nPehle chalao:  python -m pip install -r requirements.txt")
        sys.exit(1)
    app = QApplication(sys.argv)
    window = JarvisWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
