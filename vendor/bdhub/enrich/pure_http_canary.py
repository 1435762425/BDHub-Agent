# -*- coding: utf-8 -*-
"""运行哈希锁定的纯 HTTP 候选 canary；不会被生产任务自动选择。"""
from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .. import config as cfgmod
from ..account_policy import resolve_account_policy
from .identity_store import load_identity
from .profile_lease import ProfileLease


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_MANIFEST = Path(__file__).with_name("pure_http_runtime_manifest.json")
_PURE_HTTP_COOKIE_NAMES = frozenset({
    "ttspc_side_menu_role",
    "ttspc_side_menu_role_us",
    "odin_tt",
    "oec_lucifer",
    "passport_csrf_token",
    "passport_csrf_token_default",
    "s_v_web_id",
    "sessionid",
    "sessionid_ss",
    "sid_guard",
    "sid_tt",
    "store-country-sign",
    "tt_session_tlb_tag",
    "ttwid",
    "uid_tt",
    "uid_tt_ss",
    "verifyfp",
})


def _cookie_value(cookie_header: str, name: str) -> str:
    expected = name.casefold()
    for raw_part in str(cookie_header or "").split(";"):
        key, separator, value = raw_part.strip().partition("=")
        if separator and key.casefold() == expected:
            return value.strip()
    return ""


def local_signer_seed_ready(headers: dict[str, str]) -> bool:
    """判断当前身份能否无等待地启动附件的本地 Unisec 签名器。"""
    if not isinstance(headers, dict):
        return False
    normalized = {
        str(name).casefold(): str(value)
        for name, value in headers.items()
        if isinstance(name, str) and isinstance(value, str)
    }
    return bool(_cookie_value(normalized.get("cookie", ""), "oec_lucifer"))


def _pure_http_cookie_header(cookie_header: str) -> str:
    """隔离浏览器广告/语言/旧指纹 cookie，避免污染纯 HTTP 验证状态。"""
    parts: list[str] = []
    for raw_part in str(cookie_header or "").split(";"):
        key, separator, value = raw_part.strip().partition("=")
        if separator and key.casefold() in _PURE_HTTP_COOKIE_NAMES:
            parts.append(f"{key}={value}")
    return "; ".join(parts)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_runtime(
    runtime_dir: str | Path,
    *,
    project_root: str | Path = cfgmod.ROOT,
    manifest_path: str | Path = _DEFAULT_MANIFEST,
) -> Path:
    """只执行项目 data/runtime 下、逐文件哈希匹配的候选代码。"""
    root = Path(project_root).resolve()
    allowed_root = (root / "data" / "runtime").resolve()
    runtime = Path(runtime_dir).resolve()
    if not runtime.is_relative_to(allowed_root):
        raise ValueError("纯 HTTP runtime 必须位于项目 data/runtime 内")
    manifest_file = Path(manifest_path).resolve()
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("纯 HTTP runtime manifest 无效") from exc
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, dict) or not files:
        raise ValueError("纯 HTTP runtime manifest 缺少 files")
    for relative, expected in files.items():
        if not isinstance(relative, str) or not relative.strip():
            raise ValueError("runtime manifest 文件名无效")
        if not isinstance(expected, str) or not _SHA256_RE.fullmatch(expected):
            raise ValueError("runtime manifest 哈希无效")
        candidate = (runtime / relative).resolve()
        if not candidate.is_relative_to(runtime) or not candidate.is_file():
            raise ValueError("runtime manifest 文件缺失或越界")
        if _file_sha256(candidate) != expected:
            raise ValueError(f"纯 HTTP runtime 文件哈希不匹配: {relative}")
    return runtime


def _profile_types(values: tuple[int, ...]) -> tuple[int, ...]:
    if (
        not values
        or any(type(value) is not int or value <= 0 for value in values)
        or len(set(values)) != len(values)
    ):
        raise ValueError("profile_types 必须是唯一的正整数")
    return values


def build_probe_command(
    *,
    python_executable: str,
    runtime_dir: str | Path,
    targets: str | Path,
    output_dir: str | Path,
    qps: float,
    profile_types: tuple[int, ...],
    captcha_attempts: int = 1,
    limit: int | None = None,
    offset: int = 0,
    resume: bool = False,
) -> list[str]:
    if (
        isinstance(qps, bool)
        or not isinstance(qps, (int, float))
        or not math.isfinite(float(qps))
        or not 0 < float(qps) <= 3.0
    ):
        raise ValueError("qps 必须在 (0, 3.0] 范围内")
    selected_types = _profile_types(profile_types)
    if type(captcha_attempts) is not int or not 1 <= captcha_attempts <= 3:
        raise ValueError("captcha_attempts 必须在 1..3 范围内")
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError("limit 必须是正整数")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset 必须是非负整数")
    runtime = Path(runtime_dir).resolve()
    command = [
        str(python_executable),
        str(runtime / "pure_http_probe.py"),
        "--config",
        str(runtime / "config.json"),
        "--targets",
        str(Path(targets).resolve()),
        "--output-dir",
        str(Path(output_dir).resolve()),
        "--qps",
        str(float(qps)),
        "--profile-types",
        ",".join(str(value) for value in selected_types),
        "--captcha-attempts",
        str(captcha_attempts),
        "--offset",
        str(offset),
        "--account-stdin",
    ]
    if limit is not None:
        command.extend(("--limit", str(limit)))
    if resume:
        command.append("--resume")
    return command


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _atomic_write_targets(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("handle", "oec_id"))
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def partition_target_csv(
    source: str | Path,
    destination: str | Path,
    *,
    worker_count: int,
    limit: int | None = None,
) -> list[Path]:
    """把目标稳定轮询分片；仅在 gitignored runtime 中保存必要身份列。"""
    if type(worker_count) is not int or not 1 <= worker_count <= 16:
        raise ValueError("worker_count 必须在 1..16 范围内")
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError("limit 必须是正整数")
    source_path = Path(source).resolve()
    if not source_path.is_file():
        raise ValueError("目标 CSV 不存在")

    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    with source_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "handle" not in reader.fieldnames:
            raise ValueError("目标 CSV 缺少 handle 列")
        for raw in reader:
            handle = str(raw.get("handle") or "").strip().lstrip("@")
            oec_id = str(raw.get("oec_id") or "").strip()
            handle_key = handle.casefold()
            if not handle or handle_key in seen:
                continue
            if oec_id and not oec_id.isdigit():
                raise ValueError("目标 CSV 的 oec_id 格式无效")
            seen.add(handle_key)
            rows.append({"handle": handle, "oec_id": oec_id})
            if limit is not None and len(rows) >= limit:
                break
    if not rows:
        raise ValueError("目标 CSV 中没有有效记录")

    shard_rows = [[] for _ in range(min(worker_count, len(rows)))]
    for index, row in enumerate(rows):
        shard_rows[index % len(shard_rows)].append(row)
    shard_root = Path(destination).resolve()
    paths: list[Path] = []
    for index, rows_for_worker in enumerate(shard_rows, 1):
        path = shard_root / f"worker-{index:02d}.csv"
        _atomic_write_targets(path, rows_for_worker)
        paths.append(path)
    return paths


def account_stdin_payload(
    headers: dict[str, str],
    *,
    device_id: str = "",
    partner_id: str = "",
) -> dict[str, str]:
    if not isinstance(headers, dict):
        raise ValueError("账号身份请求头无效")
    normalized = {
        str(name).casefold(): str(value)
        for name, value in headers.items()
        if isinstance(name, str) and isinstance(value, str)
    }
    cookie = normalized.get("cookie", "").strip()
    user_agent = normalized.get("user-agent", "").strip()
    if not cookie:
        raise ValueError("账号身份缺少 Cookie")
    if not user_agent:
        raise ValueError("账号身份缺少 User-Agent")
    reduced_cookie = _pure_http_cookie_header(cookie)
    if not reduced_cookie:
        raise ValueError("账号身份缺少纯 HTTP 必需 Cookie")
    payload = {"cookie_header": reduced_cookie, "user_agent": user_agent}
    payload["device_id"] = str(device_id or "0").strip() or "0"
    if str(partner_id or "").strip():
        normalized_partner = str(partner_id).strip()
        if not normalized_partner.isdigit():
            raise ValueError("partner_id 格式无效")
        payload["partner_id"] = normalized_partner
    return payload


def run_canary(
    *,
    runtime_dir: str | Path,
    targets: str | Path,
    account_name: str,
    profile_types: tuple[int, ...],
    qps: float = 3.0,
    captcha_attempts: int = 1,
    limit: int | None = None,
    offset: int = 0,
    output_dir: str | Path | None = None,
    resume: bool = False,
    config_path: str | Path | None = None,
    manifest_path: str | Path = _DEFAULT_MANIFEST,
) -> int:
    runtime = validate_runtime(runtime_dir, manifest_path=manifest_path)
    target_path = Path(targets).resolve()
    if not target_path.is_file():
        raise ValueError("目标 CSV 不存在")
    cfg = cfgmod.load(config_path)
    accounts = {
        account.name: account
        for account in cfgmod.load_accounts(cfg)
        if getattr(account, "enabled", True) is not False
    }
    account = accounts.get(account_name)
    if account is None:
        raise ValueError("纯 HTTP canary 账号不存在或已停用")
    if not resolve_account_policy(account).collection_pool:
        raise ValueError("纯 HTTP canary 账号不在数据抓取池")
    bundle = load_identity(account.headers_json)
    if not local_signer_seed_ready(bundle.headers):
        raise ValueError("纯 HTTP canary 账号缺少本地签名器身份种子")
    stdin_payload = account_stdin_payload(
        bundle.headers,
        device_id=getattr(account, "device_id", ""),
        partner_id=account.partner_id,
    )
    destination = Path(output_dir).resolve() if output_dir else (
        cfgmod.ROOT
        / "data"
        / "runtime"
        / "pure-http-results"
        / datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    command = build_probe_command(
        python_executable=sys.executable,
        runtime_dir=runtime,
        targets=target_path,
        output_dir=destination,
        qps=qps,
        profile_types=profile_types,
        captcha_attempts=captcha_attempts,
        limit=limit,
        offset=offset,
        resume=resume,
    )
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    dependency_dir = runtime.parent / "pydeps"
    if dependency_dir.is_dir():
        current = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(dependency_dir) + (
            os.pathsep + current if current else ""
        )
    lease = ProfileLease(
        account.profile_dir,
        account=account.name,
        market=cfg.market,
        operation="pure-http-canary",
    )
    with lease:
        completed = subprocess.run(
            command,
            cwd=str(runtime),
            env=env,
            input=json.dumps(stdin_payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            check=False,
        )
    return int(completed.returncode)


def run_multi_account_canary(
    *,
    runtime_dir: str | Path,
    targets: str | Path,
    account_names: list[str],
    profile_types: tuple[int, ...],
    qps: float = 3.0,
    captcha_attempts: int = 3,
    limit: int | None = None,
    output_dir: str | Path | None = None,
    resume: bool = False,
    config_path: str | Path | None = None,
    manifest_path: str | Path = _DEFAULT_MANIFEST,
) -> dict[str, int]:
    """用多个独立账号并行运行候选；每号仍由自己的租约和节拍保护。"""
    runtime = validate_runtime(runtime_dir, manifest_path=manifest_path)
    clean_names = list(dict.fromkeys(
        str(name or "").strip()
        for name in account_names
        if str(name or "").strip()
    ))
    if len(clean_names) < 2:
        raise ValueError("多账号 canary 至少需要两个账号")
    if len(clean_names) > 8:
        raise ValueError("多账号 canary 最多允许八个账号")
    selected_types = _profile_types(profile_types)
    if type(captcha_attempts) is not int or not 1 <= captcha_attempts <= 3:
        raise ValueError("captcha_attempts 必须在 1..3 范围内")

    cfg = cfgmod.load(config_path)
    available = {
        account.name: account
        for account in cfgmod.load_accounts(cfg)
        if getattr(account, "enabled", True) is not False
    }
    missing = [name for name in clean_names if name not in available]
    if missing:
        raise ValueError("纯 HTTP canary 账号不存在或已停用")
    selected_accounts = [available[name] for name in clean_names]
    if any(
        not resolve_account_policy(account).collection_pool
        for account in selected_accounts
    ):
        raise ValueError("纯 HTTP canary 账号不在数据抓取池")

    destination = Path(output_dir).resolve() if output_dir else (
        cfgmod.ROOT
        / "data"
        / "runtime"
        / "pure-http-pool"
        / datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    shards = partition_target_csv(
        targets,
        destination / "targets",
        worker_count=len(selected_accounts),
        limit=limit,
    )
    assignments = list(zip(selected_accounts, shards, strict=True))
    target_count = 0
    for shard in shards:
        with shard.open("r", encoding="utf-8", newline="") as stream:
            target_count += sum(1 for _ in csv.DictReader(stream))
    _atomic_write_json(destination / "pool_manifest.json", {
        "schema_version": 1,
        "target_count": target_count,
        "worker_count": len(assignments),
        "profile_types": list(selected_types),
        "qps_per_account": float(qps),
        "captcha_attempts": captcha_attempts,
    })

    return_codes: list[int] = []
    with ThreadPoolExecutor(
        max_workers=len(assignments),
        thread_name_prefix="pure-http-account",
    ) as executor:
        futures = {
            executor.submit(
                run_canary,
                runtime_dir=runtime,
                targets=shard,
                account_name=account.name,
                profile_types=selected_types,
                qps=qps,
                captcha_attempts=captcha_attempts,
                limit=None,
                offset=0,
                output_dir=destination / f"worker-{index:02d}",
                resume=resume,
                config_path=config_path,
                manifest_path=manifest_path,
            ): index
            for index, (account, shard) in enumerate(assignments, 1)
        }
        for future in as_completed(futures):
            try:
                return_codes.append(int(future.result()))
            except Exception:
                return_codes.append(1)
    result = {
        "target_count": target_count,
        "worker_count": len(assignments),
        "succeeded_workers": sum(code == 0 for code in return_codes),
        "failed_workers": sum(code != 0 for code in return_codes),
    }
    _atomic_write_json(destination / "pool_summary.json", result)
    return result


def _parse_types(raw: str) -> tuple[int, ...]:
    try:
        return _profile_types(tuple(
            int(item.strip())
            for item in raw.split(",")
            if item.strip()
        ))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="哈希锁定的纯 HTTP code=10000 受控 canary",
    )
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--targets", required=True)
    account_group = parser.add_mutually_exclusive_group(required=True)
    account_group.add_argument("--account")
    account_group.add_argument("--accounts", help="逗号分隔的多账号并发池")
    parser.add_argument("--profile-types", required=True, type=_parse_types)
    parser.add_argument("--qps", type=float, default=3.0)
    parser.add_argument("--captcha-attempts", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--output-dir")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--config")
    parser.add_argument("--manifest", default=str(_DEFAULT_MANIFEST))
    args = parser.parse_args()
    if args.accounts:
        result = run_multi_account_canary(
            runtime_dir=args.runtime_dir,
            targets=args.targets,
            account_names=[
                item.strip()
                for item in args.accounts.split(",")
                if item.strip()
            ],
            profile_types=args.profile_types,
            qps=args.qps,
            captcha_attempts=args.captcha_attempts,
            limit=args.limit,
            output_dir=args.output_dir,
            resume=args.resume,
            config_path=args.config,
            manifest_path=args.manifest,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["failed_workers"] == 0 else 1
    return run_canary(
        runtime_dir=args.runtime_dir,
        targets=args.targets,
        account_name=args.account,
        profile_types=args.profile_types,
        qps=args.qps,
        captcha_attempts=args.captcha_attempts,
        limit=args.limit,
        offset=args.offset,
        output_dir=args.output_dir,
        resume=args.resume,
        config_path=args.config,
        manifest_path=args.manifest,
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "account_stdin_payload",
    "build_probe_command",
    "local_signer_seed_ready",
    "run_canary",
    "validate_runtime",
]
