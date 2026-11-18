"""Локальный сервер окна заметок. Слушает только этот компьютер."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from notes_desk.harness import ProviderError, Settings, run_turn, system_message
from notes_desk.sandbox import SandboxError, list_files, media_file, read_note

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 2_000_000
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class Desk:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.root: Path | None = None
        self.base_url = "https://api.openai.com/v1"
        self.model = ""
        self.api_key = ""
        self.messages: list[dict] = []

    def status(self) -> dict:
        root = str(self.root) if self.root is not None else ""
        return {
            "root": root,
            "baseUrl": self.base_url,
            "model": self.model,
            "hasKey": self.api_key != "",
        }

    def set_root(self, raw: str) -> dict:
        if not isinstance(raw, str) or raw.strip() == "":
            raise SandboxError("Укажите папку")
        path = Path(raw).expanduser()
        if not path.is_dir():
            raise SandboxError("Это не папка")
        resolved = path.resolve()
        if self.root != resolved:
            self.root = resolved
            self.messages = [system_message(resolved)]
            return {"root": str(resolved), "reset": True}
        return {"root": str(resolved), "reset": False}

    def set_settings(self, payload: dict) -> dict:
        base_url = payload.get("baseUrl", self.base_url)
        model = payload.get("model", self.model)
        if not isinstance(base_url, str) or base_url.strip() == "":
            raise SandboxError("Укажите адрес API")
        if not isinstance(model, str):
            raise SandboxError("Модель должна быть строкой")
        text = base_url.strip()
        if not text.startswith("https://") and not text.startswith("http://"):
            raise SandboxError("Адрес API должен начинаться с http:// или https://")
        if text.rstrip("/").endswith("/chat/completions"):
            raise SandboxError("Укажите корень API, без /chat/completions")
        self.base_url = text
        self.model = model.strip()
        if payload.get("apiKey") is not None:
            api_key = payload.get("apiKey")
            if not isinstance(api_key, str):
                raise SandboxError("Ключ должен быть строкой")
            self.api_key = api_key.strip()
        return self.status()

    def clear_key(self) -> dict:
        self.api_key = ""
        return self.status()

    def reset_chat(self) -> dict:
        if self.root is None:
            self.messages = []
        else:
            self.messages = [system_message(self.root)]
        return {"reset": True}

    def chat(self, text: str) -> dict:
        if not isinstance(text, str) or text.strip() == "":
            raise SandboxError("Напишите, что сделать с заметками")
        if len(text) > 20_000:
            raise SandboxError("Сообщение длиннее 20000 символов")
        if self.root is None:
            raise SandboxError("Сначала откройте папку")
        self.messages.append({"role": "user", "content": text.strip()})
        try:
            reply, steps = run_turn(
                self.messages,
                self.root,
                Settings(self.base_url, self.model, self.api_key),
            )
        except ProviderError as error:
            if error.steps:
                self.messages.append(
                    {
                        "role": "assistant",
                        "content": f"Вызов модели оборвался: {error}",
                    }
                )
            elif self.messages and self.messages[-1].get("role") == "user":
                self.messages.pop()
            raise
        return {"reply": reply, "steps": steps}


DESK = Desk()


def load_environment() -> None:
    import os

    base_url = os.environ.get("NOTES_DESK_BASE_URL")
    model = os.environ.get("NOTES_DESK_MODEL")
    api_key = os.environ.get("NOTES_DESK_API_KEY")
    if base_url:
        text = base_url.strip()
        if not text.startswith("https://") and not text.startswith("http://"):
            raise SystemExit("NOTES_DESK_BASE_URL должен начинаться с http:// или https://")
        if text.rstrip("/").endswith("/chat/completions"):
            raise SystemExit("NOTES_DESK_BASE_URL — это корень API, без /chat/completions")
        DESK.base_url = text
    if model is not None and model.strip() != "":
        DESK.model = model.strip()
    if api_key is not None and api_key.strip() != "":
        DESK.api_key = api_key.strip()


def pick_folder() -> str:
    zenity = shutil.which("zenity")
    if zenity is None:
        raise SandboxError("Окна выбора нет. Впишите путь к папке вручную")
    completed = subprocess.run(
        [zenity, "--file-selection", "--directory", "--title=Папка заметок"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SandboxError("Выбор папки отменён")
    chosen = completed.stdout.strip()
    if chosen == "":
        raise SandboxError("Папка не выбрана")
    return chosen


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._file(STATIC / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/app.css":
            self._file(STATIC / "app.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self._file(STATIC / "app.js", "text/javascript; charset=utf-8")
            return
        if parsed.path == "/api/status":
            with DESK.lock:
                self._json(200, DESK.status())
            return
        if parsed.path == "/api/files":
            self._files()
            return
        if parsed.path == "/api/text":
            self._text(parsed.query)
            return
        if parsed.path == "/api/media":
            self._media(parsed.query)
            return
        self._json(404, {"error": "Нет такой страницы"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._payload()
        except SandboxError as error:
            self._json(400, {"error": str(error)})
            return
        try:
            if parsed.path == "/api/pick-folder":
                self._pick()
                return
            if parsed.path == "/api/root":
                with DESK.lock:
                    result = DESK.set_root(payload.get("path"))
                self._json(200, result)
                return
            if parsed.path == "/api/settings":
                with DESK.lock:
                    result = DESK.set_settings(payload)
                self._json(200, result)
                return
            if parsed.path == "/api/clear-key":
                with DESK.lock:
                    result = DESK.clear_key()
                self._json(200, result)
                return
            if parsed.path == "/api/reset":
                with DESK.lock:
                    result = DESK.reset_chat()
                self._json(200, result)
                return
            if parsed.path == "/api/chat":
                with DESK.lock:
                    result = DESK.chat(payload.get("message"))
                self._json(200, result)
                return
        except SandboxError as error:
            self._json(400, {"error": str(error)})
            return
        except ProviderError as error:
            self._json(502, {"error": str(error), "steps": error.steps})
            return
        self._json(404, {"error": "Нет такого действия"})

    def log_message(self, template: str, *args) -> None:
        print(template % args)

    def _files(self) -> None:
        with DESK.lock:
            root = DESK.root
        if root is None:
            self._json(400, {"error": "Сначала откройте папку"})
            return
        self._json(200, list_files(root))

    def _text(self, query: str) -> None:
        try:
            raw = _query_path(query)
        except SandboxError as error:
            self._json(400, {"error": str(error)})
            return
        with DESK.lock:
            root = DESK.root
        if root is None:
            self._json(400, {"error": "Сначала откройте папку"})
            return
        try:
            self._json(200, read_note(root, raw))
        except SandboxError as error:
            self._json(400, {"error": str(error)})

    def _media(self, query: str) -> None:
        try:
            raw = _query_path(query)
        except SandboxError as error:
            self._json(400, {"error": str(error)})
            return
        with DESK.lock:
            root = DESK.root
        if root is None:
            self._json(400, {"error": "Сначала откройте папку"})
            return
        try:
            path = media_file(root, raw)
        except SandboxError as error:
            self._json(400, {"error": str(error)})
            return
        content_type = MEDIA_TYPES.get(path.suffix.lower())
        if content_type is None:
            self._json(400, {"error": "Этот рисунок показать нельзя"})
            return
        if path.stat().st_size > 15_000_000:
            self._json(400, {"error": "Рисунок больше 15 МБ"})
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _pick(self) -> None:
        try:
            chosen = pick_folder()
            with DESK.lock:
                result = DESK.set_root(chosen)
        except SandboxError as error:
            self._json(400, {"error": str(error)})
            return
        self._json(200, result)

    def _payload(self) -> dict:
        length = self.headers.get("Content-Length")
        if length is None:
            raise SandboxError("Нет тела запроса")
        try:
            size = int(length)
        except ValueError as error:
            raise SandboxError("Некорректный размер запроса") from error
        if size < 0 or size > MAX_BODY:
            raise SandboxError("Слишком большой запрос")
        raw = self.rfile.read(size)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise SandboxError("Тело запроса не JSON") from error
        if not isinstance(payload, dict):
            raise SandboxError("Тело запроса должно быть объектом")
        return payload

    def _file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self._json(404, {"error": "Файл окна не найден"})
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def _query_path(query: str) -> str:
    values = parse_qs(query).get("path")
    if not values or values[0].strip() == "":
        raise SandboxError("Укажите путь к файлу")
    return values[0]


def serve(host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Заметки открыты: http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")
    finally:
        server.server_close()

