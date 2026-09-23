#!/usr/bin/env python3
"""Check or rebuild the reviewed protocol snapshot, including resources and local patches.

--check is read-only. --write stages and validates the complete snapshot before an
atomic directory exchange; it never replaces unreviewed local modifications.
Update scripts/vendor-runtime/manifest.json and local.patch together when reviewing
an upstream update. No credentials, account data or arbitrary JSON are copied.
"""
from __future__ import annotations

import argparse
import ast
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEGACY = ROOT.parent / "01-BDSystem-V2"
DEFAULT_OUT = ROOT / "vendor"
DEFAULT_MANIFEST = ROOT / "scripts/vendor-runtime/manifest.json"
RESOURCE_PATH = "bdhub/enrich/pure_http_runtime_manifest.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class VendorError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_path(value: str) -> str:
    if not isinstance(value, str):
        raise VendorError("manifest_path_invalid")
    path = PurePosixPath(value)
    if (path.is_absolute() or ".." in path.parts or "\\" in value or
            str(path) != value or not value.startswith("bdhub/")):
        raise VendorError("manifest_path_invalid")
    if path.suffix != ".py" and value != RESOURCE_PATH:
        raise VendorError("manifest_resource_not_allowed")
    return value


def read_manifest(path: Path) -> tuple[dict, bytes]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise VendorError("manifest_schema_invalid")
    rows = value.get("files")
    if not isinstance(rows, list) or not rows:
        raise VendorError("manifest_files_missing")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "source", "upstreamSha256", "sha256"}:
            raise VendorError("manifest_file_invalid")
        name = checked_path(row["path"])
        if name in seen or not isinstance(row["sha256"], str) or not SHA256.fullmatch(row["sha256"]):
            raise VendorError("manifest_file_invalid")
        seen.add(name)
        if row["source"] == "empty":
            if not name.endswith("/__init__.py") or row["upstreamSha256"] is not None or row["sha256"] != digest(b""):
                raise VendorError("manifest_empty_package_invalid")
        elif row["source"] != "upstream" or not isinstance(row["upstreamSha256"], str) or not SHA256.fullmatch(row["upstreamSha256"]):
            raise VendorError("manifest_upstream_hash_invalid")
    if RESOURCE_PATH not in seen or "bdhub/__init__.py" not in seen:
        raise VendorError("manifest_runtime_resource_missing")
    previous = value.get("previousSnapshot")
    if previous is not None:
        if not isinstance(previous, dict) or RESOURCE_PATH not in previous or "bdhub/__init__.py" not in previous:
            raise VendorError("manifest_previous_snapshot_invalid")
        for name, expected in previous.items():
            checked_path(name)
            if not isinstance(expected, str) or not SHA256.fullmatch(expected):
                raise VendorError("manifest_previous_snapshot_invalid")
    patch = value.get("patch")
    if not isinstance(patch, dict) or set(patch) != {"file", "sha256"} or patch["file"] != "local.patch":
        raise VendorError("manifest_patch_invalid")
    patch_path = path.parent / patch["file"]
    if patch_path.is_symlink():
        raise VendorError("manifest_patch_symlink")
    patch_bytes = patch_path.read_bytes()
    if digest(patch_bytes) != patch["sha256"]:
        raise VendorError("manifest_patch_changed")
    return value, patch_bytes


def read_source(root: Path, name: str) -> bytes:
    root = root.resolve()
    path = root / name
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise VendorError(f"source_missing_or_outside_root: {name}")
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink():
            raise VendorError(f"source_symlink: {name}")
    return path.read_bytes()


def validate_resource(data: bytes) -> None:
    value = json.loads(data)
    files = value.get("files") if isinstance(value, dict) else None
    if not isinstance(files, dict) or not files:
        raise VendorError("runtime_resource_files_missing")
    for name, expected in files.items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or not isinstance(expected, str) or not SHA256.fullmatch(expected):
            raise VendorError("runtime_resource_invalid")


def validate_snapshot(out: Path, manifest: dict) -> list[str]:
    errors = []
    target = out / "bdhub"
    expected = {row["path"]: row for row in manifest["files"]}
    present = set()
    if target.is_symlink():
        return ["target_symlink"]
    if target.is_dir():
        for path in target.rglob("*"):
            relative = path.relative_to(out)
            if "__pycache__" in relative.parts or path.suffix == ".pyc":
                continue
            if path.is_symlink():
                errors.append(f"target_symlink: {relative}")
            elif path.is_file():
                present.add(relative.as_posix())
    errors.extend(f"missing: {name}" for name in sorted(expected.keys() - present))
    errors.extend(f"extra: {name}" for name in sorted(present - expected.keys()))
    for name in sorted(present & expected.keys()):
        data = (out / name).read_bytes()
        if digest(data) != expected[name]["sha256"]:
            errors.append(f"changed: {name}")
            continue
        try:
            if name.endswith(".py"):
                ast.parse(data, filename=name)
            else:
                validate_resource(data)
        except (SyntaxError, ValueError, TypeError) as error:
            errors.append(f"invalid: {name}: {type(error).__name__}")
    return errors


def verify_upstream(legacy: Path, manifest: dict) -> dict[str, bytes]:
    files = {}
    for row in manifest["files"]:
        data = b"" if row["source"] == "empty" else read_source(legacy, row["path"])
        if row["source"] == "upstream" and digest(data) != row["upstreamSha256"]:
            raise VendorError(f"upstream_changed_review_required: {row['path']}")
        files[row["path"]] = data
    return files


def existing_snapshot_errors(out: Path, manifest: dict) -> list[str]:
    """Allow only the desired snapshot or an explicitly reviewed previous revision."""
    errors = validate_snapshot(out, manifest)
    if errors and manifest.get("previousSnapshot"):
        previous = {"files": [{"path": name, "sha256": expected}
                              for name, expected in manifest["previousSnapshot"].items()]}
        if not validate_snapshot(out, previous):
            return []
    return errors


def stage_snapshot(stage: Path, files: dict[str, bytes], patch: bytes, manifest: dict) -> None:
    for name, data in files.items():
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if patch:
        # Do not let a containing checkout or inherited Git environment redirect the patch.
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env["GIT_CEILING_DIRECTORIES"] = str(stage.parent.resolve())
        for check in (True, False):
            command = ["git", "apply", "--whitespace=nowarn", *(["--check"] if check else []), "-"]
            result = subprocess.run(command, cwd=stage, input=patch, capture_output=True, check=False, env=env)
            if result.returncode:
                raise VendorError("local_patch_apply_failed")
    errors = validate_snapshot(stage, manifest)
    if errors:
        raise VendorError("staged_snapshot_invalid: " + "; ".join(errors))


def atomic_publish(staged: Path, target: Path) -> None:
    """Exchange directories without a missing-package interval; fail closed if unsupported."""
    if not target.exists():
        os.replace(staged, target)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        swap = libc.renamex_np
        swap.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = swap(os.fsencode(staged), os.fsencode(target), 2)  # RENAME_SWAP
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        swap = libc.renameat2
        swap.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = swap(-100, os.fsencode(staged), -100, os.fsencode(target), 2)  # RENAME_EXCHANGE
    else:
        raise VendorError("atomic_directory_exchange_unavailable")
    if result != 0:
        raise VendorError(f"atomic_directory_exchange_failed: errno={ctypes.get_errno()}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy", type=Path, default=DEFAULT_LEGACY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true", help="read-only verification (default)")
    args = parser.parse_args(argv)
    try:
        target = args.out / "bdhub"
        upstream = (args.legacy / "bdhub").resolve()
        if target.resolve().is_relative_to(upstream) or upstream.is_relative_to(target.resolve()):
            raise VendorError("source_target_overlap")
        manifest, patch = read_manifest(args.manifest)
        source = verify_upstream(args.legacy, manifest)
        errors = validate_snapshot(args.out, manifest)
        if not args.write:
            if errors:
                raise VendorError("\n".join(errors))
            print(f"vendor ok: {len(manifest['files'])} files, hashes/resources/local patches verified")
            return 0
        if target.exists():
            existing_errors = existing_snapshot_errors(args.out, manifest)
            if existing_errors:
                raise VendorError("existing_snapshot_changed_review_required: " + "; ".join(existing_errors))
        if target.is_symlink() or args.out.is_symlink():
            raise VendorError("target_symlink")
        args.out.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".vendor-stage-", dir=args.out) as directory:
            stage = Path(directory)
            stage_snapshot(stage, source, patch, manifest)
            if target.exists() and existing_snapshot_errors(args.out, manifest):
                raise VendorError("existing_snapshot_changed_during_build")
            atomic_publish(stage / "bdhub", target)
        print(f"vendor rebuilt: {len(manifest['files'])} verified files; local patches preserved")
        return 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
