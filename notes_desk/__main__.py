"""Запуск окна заметок или самопроверки без модели."""

from __future__ import annotations

import argparse
import threading
import webbrowser

from notes_desk import __version__
from notes_desk.harness import self_check as check_harness
from notes_desk.sandbox import self_check as check_sandbox
from notes_desk.server import load_environment, serve


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Локальный harness для текстовых заметок")
    parser.add_argument("--version", action="version", version=f"notes-desk {__version__}")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--check", action="store_true", help="Проверить доступ к файлам и цикл без API")
    args = parser.parse_args(argv)
    if args.check:
        check_sandbox()
        check_harness()
        print("Проверка прошла")
        return
    if args.port < 1 or args.port > 65535:
        raise SystemExit("Порт должен быть от 1 до 65535")
    load_environment()
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        serve("127.0.0.1", args.port)
    except OSError as error:
        raise SystemExit(f"Не удалось открыть порт {args.port}: {error}") from error


if __name__ == "__main__":
    main()
