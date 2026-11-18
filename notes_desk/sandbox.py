"""Доступ к файлам только внутри открытой папки."""

from __future__ import annotations

import os
from pathlib import Path

NOTE_SUFFIXES = {".md", ".markdown", ".txt"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_FILES = 400
MAX_TEXT_CHARS = 200_000
SKIP_DIRS = {".git", "node_modules", "__pycache__"}


class SandboxError(Exception):
    """Запрос выходит за правила открытой папки."""


def kind_of(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in NOTE_SUFFIXES:
        return "note"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    return "other"


def locate(root: Path, raw: str) -> Path:
    if not isinstance(raw, str) or raw.strip() == "":
        raise SandboxError("Укажите путь к файлу")
    if "\x00" in raw:
        raise SandboxError("В пути пустой символ")
    root_real = root.resolve()
    given = Path(raw)
    candidate = given if given.is_absolute() else root_real / given
    try:
        resolved = candidate.resolve()
    except OSError as error:
        raise SandboxError(f"Путь не открывается: {error}") from error
    if resolved != root_real and root_real not in resolved.parents:
        raise SandboxError("Путь вне открытой папки")
    return resolved


def relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def list_files(root: Path) -> dict:
    root_real = root.resolve()
    items: list[dict] = []
    truncated = False
    skipped = 0
    for dirpath, dirnames, filenames in os.walk(root_real):
        current = Path(dirpath).resolve()
        if current != root_real and root_real not in current.parents:
            dirnames.clear()
            continue
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".") and name not in SKIP_DIRS
        )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = Path(dirpath) / name
            try:
                resolved = path.resolve()
            except OSError:
                skipped += 1
                continue
            if resolved != root_real and root_real not in resolved.parents:
                skipped += 1
                continue
            if not resolved.is_file():
                continue
            items.append(
                {
                    "path": resolved.relative_to(root_real).as_posix(),
                    "kind": kind_of(resolved.name),
                    "bytes": resolved.stat().st_size,
                }
            )
            if len(items) >= MAX_FILES:
                truncated = True
                break
        if truncated:
            break
    items.sort(key=lambda item: item["path"])
    return {"files": items, "truncated": truncated, "skipped": skipped}


def read_note(root: Path, raw: str) -> dict:
    path = locate(root, raw)
    if kind_of(path.name) != "note":
        raise SandboxError("Читать можно только заметки .md и .txt")
    if not path.is_file():
        raise SandboxError("Файла нет")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as error:
        raise SandboxError("Файл не в UTF-8") from error
    if len(text) > MAX_TEXT_CHARS:
        raise SandboxError(
            f"Заметка длиннее {MAX_TEXT_CHARS} символов, целиком её не читаю"
        )
    return {"path": relative(root, path), "content": text}


def write_note(root: Path, raw: str, content: str) -> dict:
    if not isinstance(content, str):
        raise SandboxError("Текст заметки должен быть строкой")
    if len(content) > MAX_TEXT_CHARS:
        raise SandboxError(f"Текст длиннее {MAX_TEXT_CHARS} символов")
    path = locate(root, raw)
    if kind_of(path.name) != "note":
        raise SandboxError("Создавать можно только заметки .md и .txt")
    if path.exists():
        raise SandboxError("Файл уже есть. Для правки используй замену фрагмента")
    if not path.parent.is_dir():
        parent = path.parent
        parent_real = parent.resolve()
        root_real = root.resolve()
        if parent_real != root_real and root_real not in parent_real.parents:
            raise SandboxError("Папка для заметки вне открытой папки")
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise SandboxError(f"Не удалось создать папку: {error}") from error
    _write_text(path, content)
    return {"path": relative(root, path), "created": True}


def replace_in_note(root: Path, raw: str, old: str, new: str) -> dict:
    if not isinstance(old, str) or old == "":
        raise SandboxError("Нужен непустой фрагмент, который надо заменить")
    if not isinstance(new, str):
        raise SandboxError("Новый текст должен быть строкой")
    path = locate(root, raw)
    if kind_of(path.name) != "note":
        raise SandboxError("Править можно только заметки .md и .txt")
    if not path.is_file():
        raise SandboxError("Файла нет")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as error:
        raise SandboxError("Файл не в UTF-8") from error
    count = text.count(old)
    if count == 0:
        raise SandboxError("Фрагмент не найден")
    if count > 1:
        raise SandboxError(
            f"Фрагмент встречается {count} раз. Нужен кусок, который есть только один раз"
        )
    updated = text.replace(old, new, 1)
    if len(updated) > MAX_TEXT_CHARS:
        raise SandboxError(f"После правки заметка длиннее {MAX_TEXT_CHARS} символов")
    _write_text(path, updated)
    return {"path": relative(root, path), "replaced": True}


def media_file(root: Path, raw: str) -> Path:
    path = locate(root, raw)
    if kind_of(path.name) != "image":
        raise SandboxError("Это не рисунок png, jpg, gif или webp")
    if not path.is_file():
        raise SandboxError("Файла нет")
    return path


def _write_text(path: Path, content: str) -> None:
    temporary = path.parent / f".{path.name}.notes-desk-tmp"
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise SandboxError(f"Не удалось записать файл: {error}") from error


def self_check() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        outside = Path(tempfile.mkdtemp())
        try:
            secret = outside / "secret.md"
            secret.write_text("секрет", encoding="utf-8")
            (root / "лекция.md").write_text("было\n", encoding="utf-8")
            (root / "схема.png").write_bytes(b"\x89PNG\r\n")
            link = root / "наружу.md"
            link.symlink_to(secret)

            listed = list_files(root)
            paths = {item["path"] for item in listed["files"]}
            if "лекция.md" not in paths or "схема.png" not in paths:
                raise SystemExit("список файлов не видит заметку или рисунок")
            if any(item["path"].endswith("secret.md") for item in listed["files"]):
                raise SystemExit("список показывает файл вне папки")

            for raw in ("../secret.md", str(secret), "наружу.md", "лекция.md\x00"):
                try:
                    locate(root, raw if "\x00" not in raw else "лекция.md\x00")
                except SandboxError:
                    pass
                else:
                    if raw != "наружу.md":
                        raise SystemExit(f"путь не отклонён: {raw}")
                    target = locate(root, raw)
                    if target == secret or outside in target.parents:
                        raise SystemExit("ссылка наружу открылась")

            try:
                read_note(root, "наружу.md")
            except SandboxError:
                pass
            else:
                raise SystemExit("заметка по ссылке наружу прочиталась")

            created = write_note(root, "новая/конспект.md", "конспект\n")
            if created["path"] != "новая/конспект.md":
                raise SystemExit("новая заметка легла не туда")
            try:
                write_note(root, "новая/конспект.md", "ещё раз\n")
            except SandboxError:
                pass
            else:
                raise SystemExit("повторная запись поверх файла прошла")
            try:
                write_note(root, "картинка.png", "нет")
            except SandboxError:
                pass
            else:
                raise SystemExit("запись картинки прошла")

            replace_in_note(root, "лекция.md", "было", "стало")
            if (root / "лекция.md").read_text(encoding="utf-8") != "стало\n":
                raise SystemExit("замена не записалась")
            replace_in_note(root, "лекция.md", "стало", "аа")
            try:
                replace_in_note(root, "лекция.md", "а", "б")
            except SandboxError:
                text = (root / "лекция.md").read_text(encoding="utf-8")
                if text != "аа\n":
                    raise SystemExit("неудачная замена изменила файл")
            else:
                raise SystemExit("неуникальный фрагмент заменился")
        finally:
            secret.unlink(missing_ok=True)
            outside.rmdir()
