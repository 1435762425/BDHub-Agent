#!/usr/bin/env python3
"""Validate the canonical documentation set and local Markdown links."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = (
    ROOT / "AGENTS.md",
    ROOT / "README.md",
    ROOT / "docs/PROJECT.md",
    ROOT / "docs/TECHNICAL.md",
    ROOT / "docs/README.md",
    ROOT / "docs/handoff/codex-takeover-20260919.md",
)
LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
REMOTE_PREFIXES = ("#", "http://", "https://", "mailto:")


def markdown_files() -> list[Path]:
    files = [ROOT / "AGENTS.md", ROOT / "README.md", ROOT / "apps/web/README.md"]
    files.extend((ROOT / "docs").rglob("*.md"))
    return sorted({path.resolve() for path in files if path.is_file()})


def main() -> int:
    errors: list[str] = []
    for path in CANONICAL:
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            errors.append(f"canonical document missing or empty: {path.relative_to(ROOT)}")

    files = markdown_files()
    for path in files:
        text = path.read_text(encoding="utf-8")
        for match in LINK.finditer(text):
            raw = match.group(1).strip()
            if not raw or raw.startswith(REMOTE_PREFIXES):
                continue
            target = raw.split("#", 1)[0]
            if not target:
                continue
            resolved = (path.parent / target).resolve()
            try:
                resolved.relative_to(ROOT)
            except ValueError:
                continue
            if not resolved.exists():
                line = text.count("\n", 0, match.start()) + 1
                errors.append(f"{path.relative_to(ROOT)}:{line}: missing link target {raw}")

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"documentation ok: {len(CANONICAL)} canonical files, {len(files)} Markdown files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
