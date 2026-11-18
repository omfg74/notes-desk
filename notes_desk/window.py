"""Нативное окно. На Linux, Windows и macOS его рисует системный webview."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

from notes_desk.server import serve

LOCAL_TYPELIBS = Path.home() / ".local/lib/girepository-1.0"


def run(port: int, serve_only: bool) -> None:
    errors: list[BaseException] = []

    def target() -> None:
        try:
            serve("127.0.0.1", port)
        except OSError as error:
            errors.append(error)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    _wait_until_ready(port, errors)
    if serve_only:
        try:
            thread.join()
        except KeyboardInterrupt:
            raise SystemExit(0) from None
        return
    _open(f"http://127.0.0.1:{port}")


def _wait_until_ready(port: int, errors: list[BaseException]) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if errors:
            raise SystemExit(f"Не удалось открыть порт {port}: {errors[0]}") from errors[0]
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    if errors:
        raise SystemExit(f"Не удалось открыть порт {port}: {errors[0]}") from errors[0]
    raise SystemExit("Сервер не ответил, окно не открыто")


def _open(url: str) -> None:
    _prepare_linux_typelibs()
    try:
        import webview
    except ImportError as error:
        raise SystemExit("Не установлен pywebview, окно открыть нельзя") from error
    webview.create_window(
        "Заметки",
        url,
        width=1200,
        height=800,
        min_size=(880, 640),
        text_select=True,
    )
    try:
        webview.start()
    except Exception as error:
        raise SystemExit(f"Не удалось открыть окно: {error}") from error


def _prepare_linux_typelibs() -> None:
    if sys.platform != "linux":
        return
    if _typelib_installed("WebKit2-4.1.typelib"):
        return
    local = LOCAL_TYPELIBS / "WebKit2-4.1.typelib"
    if not local.is_file():
        raise SystemExit(
            "Для окна на Linux нужен пакет gir1.2-webkit2-4.1 "
            "(и gir1.2-javascriptcoregtk-4.1). "
            "Команда: sudo apt install gir1.2-webkit2-4.1 gir1.2-javascriptcoregtk-4.1"
        )
    current = os.environ.get("GI_TYPELIB_PATH", "")
    os.environ["GI_TYPELIB_PATH"] = (
        str(LOCAL_TYPELIBS) if current == "" else str(LOCAL_TYPELIBS) + os.pathsep + current
    )


def _typelib_installed(name: str) -> bool:
    roots = [
        Path("/usr/lib/girepository-1.0"),
        Path("/usr/lib/x86_64-linux-gnu/girepository-1.0"),
        Path("/usr/lib/aarch64-linux-gnu/girepository-1.0"),
    ]
    for item in os.environ.get("GI_TYPELIB_PATH", "").split(os.pathsep):
        if item != "":
            roots.append(Path(item))
    return any((root / name).is_file() for root in roots)
