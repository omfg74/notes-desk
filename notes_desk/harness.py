"""Цикл: модель просит правку заметки, harness её выполняет и возвращает результат."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from notes_desk.sandbox import (
    SandboxError,
    list_files,
    read_note,
    replace_in_note,
    write_note,
)

MAX_ROUNDS = 8
PROVIDER_TIMEOUT = 120

SYSTEM_PROMPT = """\
Ты редактор текстовых заметок в одной открытой папке.
Открытая папка: {root}

Ты можешь только читать список файлов, читать заметки .md и .txt, создавать новые заметки и заменять в заметке один уникальный фрагмент.
Рисунки можно упоминать уже существующим путём в Markdown. Создавать, удалять и переименовывать рисунки нельзя.
Команд, сети и файлов вне открытой папки нет.

Перед правкой прочитай заметку. Не выдумывай номера стандартов и факты, которых нет в заметках или в сообщении человека.
Пиши по-русски, коротко и по делу, если человек не попросил другой язык или стиль.
Сначала сделай правки инструментами. В конце перечисли файлы, которые изменились, и что в них сделано.
Если фрагмент встречается несколько раз, выбери более длинный уникальный кусок. Не переписывай файл целиком через замену всего текста, если хватает короткого фрагмента.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Список файлов в открытой папке.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Прочитать текстовую заметку целиком.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Путь внутри открытой папки"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Создать новую заметку .md или .txt. Не затирает существующий файл.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "Заменить в заметке фрагмент, который встречается ровно один раз.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                },
                "required": ["path", "old", "new"],
                "additionalProperties": False,
            },
        },
    },
]


class ProviderError(Exception):
    """Модель или её API не ответили пригодным результатом."""

    def __init__(self, message: str, steps: list[dict] | None = None):
        super().__init__(message)
        self.steps = steps or []


@dataclass
class Settings:
    base_url: str
    model: str
    api_key: str


Complete = Callable[[list[dict], Settings], dict]


def system_message(root: Path) -> dict:
    return {"role": "system", "content": SYSTEM_PROMPT.format(root=root)}


def run_turn(
    messages: list[dict],
    root: Path,
    settings: Settings,
    complete: Complete | None = None,
) -> tuple[str, list[dict]]:
    call = complete or complete_chat
    steps: list[dict] = []
    for _ in range(MAX_ROUNDS):
        try:
            message = call(messages, settings)
            tool_calls = message.get("tool_calls") or []
            if not isinstance(tool_calls, list):
                raise ProviderError("API вернул инструменты в неизвестном виде")
            messages.append(_slim(message))
        except ProviderError as error:
            error.steps = steps
            raise
        if not tool_calls:
            return _text(message.get("content")), steps
        for call_item in tool_calls:
            step, tool_message = _run_tool(root, call_item)
            steps.append(step)
            messages.append(tool_message)
    stop = "Остановился после 8 шагов с инструментами. Записи, которые уже прошли, перечислены в шагах."
    messages.append({"role": "assistant", "content": stop})
    return stop, steps


def dispatch(root: Path, name: str, arguments: dict) -> dict:
    if name == "list_files":
        return list_files(root)
    if name == "read_file":
        return read_note(root, _arg(arguments, "path"))
    if name == "write_file":
        return write_note(root, _arg(arguments, "path"), _arg(arguments, "content"))
    if name == "replace_in_file":
        return replace_in_note(
            root,
            _arg(arguments, "path"),
            _arg(arguments, "old"),
            _arg(arguments, "new"),
        )
    raise SandboxError(f"Неизвестный инструмент {name}")


def complete_chat(messages: list[dict], settings: Settings) -> dict:
    if settings.model.strip() == "":
        raise ProviderError("Не задана модель")
    if settings.api_key.strip() == "":
        raise ProviderError("Не задан ключ API")
    url = _chat_url(settings.base_url)
    body = {
        "model": settings.model,
        "messages": messages,
        "tools": TOOLS,
    }
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.api_key}",
            "User-Agent": "notes-desk",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=PROVIDER_TIMEOUT) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise ProviderError(f"API ответил {error.code}: {detail}") from error
    except URLError as error:
        raise ProviderError(f"API недоступен: {error.reason}") from error
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ProviderError("API вернул не JSON") from error
    if not isinstance(parsed, dict):
        raise ProviderError("API вернул не объект")
    if parsed.get("error"):
        raise ProviderError(f"API вернул ошибку: {parsed['error']}")
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderError("API не вернул ответ модели")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise ProviderError("API вернул ответ без сообщения")
    return message


def _chat_url(base_url: str) -> str:
    text = base_url.strip()
    if not text.startswith("https://") and not text.startswith("http://"):
        raise ProviderError("Адрес API должен начинаться с http:// или https://")
    if text.rstrip("/").endswith("/chat/completions"):
        raise ProviderError("Укажите корень API, без /chat/completions")
    return text.rstrip("/") + "/chat/completions"


def _run_tool(root: Path, call_item: dict) -> tuple[dict, dict]:
    function = call_item.get("function") if isinstance(call_item, dict) else None
    name = function.get("name") if isinstance(function, dict) else None
    raw_arguments = function.get("arguments") if isinstance(function, dict) else None
    call_id = call_item.get("id") if isinstance(call_item, dict) else None
    if not isinstance(name, str) or not isinstance(call_id, str) or call_id == "":
        result = {"error": "Вызов инструмента без имени или id"}
        return (
            {"tool": name or "?", "ok": False, "detail": result["error"]},
            {
                "role": "tool",
                "tool_call_id": call_id or "missing",
                "content": json.dumps(result, ensure_ascii=False),
            },
        )
    try:
        arguments = _arguments(raw_arguments)
        result = dispatch(root, name, arguments)
        detail = _detail(name, result)
        ok = True
    except (SandboxError, ProviderError) as error:
        result = {"error": str(error)}
        detail = str(error)
        ok = False
    except (TypeError, ValueError) as error:
        result = {"error": str(error)}
        detail = str(error)
        ok = False
    return (
        {"tool": name, "ok": ok, "detail": detail},
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps(result, ensure_ascii=False),
        },
    )


def _arguments(raw) -> dict:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise SandboxError("Аргументы инструмента должны быть объектом")
        return parsed
    raise SandboxError("Аргументы инструмента в неизвестном виде")


def _arg(arguments: dict, key: str):
    if key not in arguments:
        raise SandboxError(f"Нет аргумента {key}")
    return arguments[key]


def _detail(name: str, result: dict) -> str:
    path = result.get("path")
    if name == "list_files":
        files = result.get("files")
        count = len(files) if isinstance(files, list) else 0
        return f"файлов: {count}"
    if name == "read_file" and isinstance(path, str):
        content = result.get("content")
        length = len(content) if isinstance(content, str) else 0
        return f"прочитан {path}, {length} символов"
    if name == "write_file" and isinstance(path, str):
        return f"создан {path}"
    if name == "replace_in_file" and isinstance(path, str):
        return f"заменено в {path}"
    return name


def _slim(message: dict) -> dict:
    slim = {"role": "assistant", "content": _text(message.get("content"))}
    tool_calls = message.get("tool_calls")
    if tool_calls:
        slim["tool_calls"] = tool_calls
    return slim


def _text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
                continue
            raise ProviderError("Ответ модели в неизвестном формате")
        return "".join(parts)
    raise ProviderError("Ответ модели в неизвестном формате")


def self_check() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        settings = Settings(base_url="http://127.0.0.1", model="test", api_key="test")
        state = {"n": 0}

        def fake(messages: list[dict], _settings: Settings) -> dict:
            state["n"] += 1
            if state["n"] == 1:
                return {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": json.dumps(
                                    {"path": "a.md", "content": "привет\n"},
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ],
                }
            tool_messages = [item for item in messages if item.get("role") == "tool"]
            if not tool_messages:
                raise SystemExit("результат инструмента не вернулся в историю")
            return {"role": "assistant", "content": "Создал a.md"}

        messages = [system_message(root), {"role": "user", "content": "создай заметку"}]
        reply, steps = run_turn(messages, root, settings, complete=fake)
        if reply != "Создал a.md":
            raise SystemExit(f"неожиданный ответ: {reply}")
        if not steps or steps[0]["ok"] is not True:
            raise SystemExit("шаг записи не отмечен")
        if (root / "a.md").read_text(encoding="utf-8") != "привет\n":
            raise SystemExit("цикл не записал файл")

        def fail(_messages: list[dict], _settings: Settings) -> dict:
            raise ProviderError("нет связи")

        broken = [system_message(root)]
        try:
            run_turn(broken, root, settings, complete=fail)
        except ProviderError as error:
            if str(error) != "нет связи":
                raise SystemExit("ошибка API подменена") from error
        else:
            raise SystemExit("ошибка API проглочена")
