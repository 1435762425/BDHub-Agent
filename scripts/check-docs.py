#!/usr/bin/env python3
"""Validate canonical documents and local links in all non-ignored repository Markdown."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = (
    ROOT / "AGENTS.md",
    ROOT / "README.md",
    ROOT / "docs/PROJECT.md",
    ROOT / "docs/TECHNICAL.md",
    ROOT / "docs/README.md",
    ROOT / "docs/handoff/current.md",
)
LINK = re.compile(r"!?\[[^\]]*\]\((<[^>]+>|[^)]+)\)")
HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
REMOTE_PREFIXES = ("http://", "https://", "mailto:")


def markdown_files(root: Path = ROOT) -> list[Path]:
    # Include new documents before git add, while excluding var/, dependencies and builds.
    result = subprocess.run(["git", "-C", str(root), "ls-files", "--cached", "--others",
                             "--exclude-standard", "-z"], capture_output=True, check=True)
    paths = {root / name for name in result.stdout.decode().split("\0") if name.endswith(".md")}
    return sorted(path for path in paths if path.is_file())


def heading_anchors(text: str) -> set[str]:
    anchors = set()
    for title in HEADING.findall(text):
        title = re.sub(r"<[^>]*>", "", title).lower()
        slug = re.sub(r"[^\w\- ]", "", title).replace(" ", "-")
        anchor, index = slug, 0
        while anchor in anchors:
            index += 1
            anchor = f"{slug}-{index}"
        anchors.add(anchor)
    anchors.update(re.findall(r'\b(?:id|name)=["\']([^"\']+)["\']', text))
    return anchors


def local_target(raw: str) -> tuple[str, str] | None:
    raw = raw.strip()
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1]
    else:
        raw = re.split(r'\s+["\']', raw, maxsplit=1)[0]
    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc:
        return None
    return unquote(parsed.path), unquote(parsed.fragment)


def validate_links(files: list[Path], root: Path = ROOT) -> list[str]:
    errors = []
    anchor_cache = {}
    root = root.resolve()
    for path in files:
        path = path.resolve()
        if not path.resolve().is_relative_to(root):
            errors.append(f"document outside repository: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        for match in LINK.finditer(text):
            target = local_target(match.group(1))
            if target is None:
                continue
            name, anchor = target
            resolved = (path.parent / name).resolve() if name else path.resolve()
            if not resolved.is_relative_to(root):
                continue  # Historical references to the read-only sibling repository.
            line = text.count("\n", 0, match.start()) + 1
            prefix = f"{path.relative_to(root)}:{line}"
            if not resolved.exists():
                errors.append(f"{prefix}: missing link target {match.group(1)}")
            elif anchor and resolved.suffix == ".md":
                if resolved not in anchor_cache:
                    anchor_cache[resolved] = heading_anchors(resolved.read_text(encoding="utf-8"))
                if anchor not in anchor_cache[resolved]:
                    errors.append(f"{prefix}: missing heading anchor {match.group(1)}")
    return errors


def main() -> int:
    errors = []
    for path in CANONICAL:
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            errors.append(f"canonical document missing or empty: {path.relative_to(ROOT)}")
    files = markdown_files()
    errors.extend(validate_links(files))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"documentation ok: {len(CANONICAL)} canonical files, {len(files)} Markdown files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
