"""Тег vX.Y.Z должен совпадать с номером в пакете. Запускает GitHub Actions."""

from __future__ import annotations

import ast
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def package_version() -> str:
    source = (ROOT / "notes_desk" / "__init__.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__version__":
                value = ast.literal_eval(node.value)
                if not isinstance(value, str) or value == "":
                    raise SystemExit("notes_desk/__init__.py: пустая версия")
                return value
    raise SystemExit("В notes_desk/__init__.py нет __version__")


def project_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = data["project"]["version"]
    if not isinstance(version, str) or version == "":
        raise SystemExit("pyproject.toml: пустая версия")
    return version


def main() -> None:
    code = package_version()
    project = project_version()
    if code != project:
        raise SystemExit(f"Версии расходятся: pyproject.toml {project}, пакет {code}")
    tag = os.environ.get("GITHUB_REF_NAME", "")
    if tag == "":
        print(f"версия {code}")
        return
    expected = f"v{code}"
    if tag != expected:
        raise SystemExit(f"Тег {tag} не равен {expected}")
    print(f"{tag} совпадает с версией {code}")


if __name__ == "__main__":
    main()
