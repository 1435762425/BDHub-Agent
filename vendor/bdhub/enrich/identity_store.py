from __future__ import annotations

import base64
import errno
import json
import math
import os
import re
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from bdhub.config import validate_account_name
from bdhub.hub.markets import MARKETS


_SESSION_MARKERS = (
    "sessionid=",
    "sid_tt=",
    "sid_guard=",
    "sessionid_ss=",
)
_SESSION_COOKIE_NAMES = frozenset(
    marker.removesuffix("=") for marker in _SESSION_MARKERS
)
_BACKUP_TIMESTAMP_RE = re.compile(r"^\d{8}T\d{12}Z$")
_BACKUP_SEQUENCE_WIDTH = 8
_MAX_BACKUP_CREATE_ATTEMPTS = 1_000
_IDENTITY_META_FIELDS = frozenset(
    {
        "schema_version",
        "account",
        "market",
        "verified_at",
        "verification_method",
    }
)
_BROWSER_SESSION_FILE = ".bdhub-browser-session.{market}.json"
_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_COOKIE_SAME_SITE = frozenset({"Strict", "Lax", "None"})
_TIKTOK_COOKIE_ROOTS = ("tiktok.com", "tiktokshop.com")
_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_WINDOWS_PRIVATE_ACL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$targetToken = '__BDHUB_TARGET_TOKEN__'
$kind = '__BDHUB_TARGET_KIND__'
$target = [System.Text.Encoding]::UTF8.GetString(
    [System.Convert]::FromBase64String($targetToken)
)
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$acl = Get-Acl -LiteralPath $target
$acl.SetAccessRuleProtection($true, $false)
foreach ($rule in @($acl.Access)) {
    [void]$acl.RemoveAccessRuleAll($rule)
}
$inheritance = [System.Security.AccessControl.InheritanceFlags]::None
if ($kind -eq 'directory') {
    $inheritance = (
        [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    )
}
$accessRule = [System.Security.AccessControl.FileSystemAccessRule]::new(
    $identity,
    [System.Security.AccessControl.FileSystemRights]::FullControl,
    $inheritance,
    [System.Security.AccessControl.PropagationFlags]::None,
    [System.Security.AccessControl.AccessControlType]::Allow
)
$acl.SetAccessRule($accessRule)
Set-Acl -LiteralPath $target -AclObject $acl
""".strip()


@dataclass(frozen=True, slots=True)
class IdentityBundle:
    headers: dict[str, str]
    browser_cookies: tuple[dict, ...] = ()

    @property
    def cookie(self) -> str:
        for name, value in self.headers.items():
            if isinstance(name, str) and name.casefold() == "cookie":
                return value if isinstance(value, str) else ""
        return ""

    @property
    def user_agent(self) -> str:
        for name, value in self.headers.items():
            if isinstance(name, str) and name.casefold() == "user-agent":
                return value if isinstance(value, str) else ""
        return ""


@dataclass(frozen=True, slots=True)
class IdentityMeta:
    account: str
    market: str
    verified_at: str
    verification_method: str
    schema_version: int = 1


def _validate_meta(meta: IdentityMeta) -> None:
    if type(meta.schema_version) is not int or meta.schema_version != 1:
        raise ValueError("身份元数据 schema_version 必须为 1")

    try:
        validate_account_name(meta.account)
    except ValueError:
        raise ValueError("身份元数据账号名不安全") from None

    if not isinstance(meta.market, str) or meta.market not in MARKETS:
        raise ValueError("身份元数据市场不受支持")

    verified_at = meta.verified_at
    if not isinstance(verified_at, str) or not verified_at.strip():
        raise ValueError("身份元数据验证时间必须为非空 ISO-8601 字符串")
    try:
        parsed_verified_at = datetime.fromisoformat(verified_at)
    except (ValueError, OverflowError):
        raise ValueError("身份元数据验证时间格式无效") from None
    if (
        parsed_verified_at.tzinfo is None
        or parsed_verified_at.utcoffset() is None
    ):
        raise ValueError("身份元数据验证时间必须包含时区")

    verification_method = meta.verification_method
    if (
        not isinstance(verification_method, str)
        or not verification_method.strip()
    ):
        raise ValueError("身份元数据验证方法必须为非空字符串")
    if "\r" in verification_method or "\n" in verification_method:
        raise ValueError("身份元数据验证方法不能包含换行符")
    if len(verification_method) > 128:
        raise ValueError("身份元数据验证方法超过长度限制")


def validate_identity(bundle: IdentityBundle) -> None:
    if not isinstance(bundle.headers, dict):
        raise ValueError("身份请求头必须是字典")

    seen_header_names: set[str] = set()
    for name, value in bundle.headers.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError("请求头名称和内容必须是字符串")
        normalized_name = name.casefold()
        if normalized_name in seen_header_names:
            raise ValueError("请求头名称不能重复")
        seen_header_names.add(normalized_name)
        if "\r" in name or "\n" in name or "\r" in value or "\n" in value:
            raise ValueError("请求头不能包含换行符")

    cookie = bundle.cookie
    if not cookie:
        raise ValueError("身份 Cookie 不能为空")
    try:
        cookie_size = len(cookie.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("身份 Cookie 不是有效的 UTF-8 文本") from None
    if cookie_size > 64 * 1024:
        raise ValueError("身份 Cookie 超过长度限制")
    if "\r" in cookie or "\n" in cookie:
        raise ValueError("身份 Cookie 不能包含换行符")
    has_session_cookie = False
    for cookie_pair in cookie.split(";"):
        name, separator, value = cookie_pair.strip().partition("=")
        if separator and name in _SESSION_COOKIE_NAMES and value:
            has_session_cookie = True
            break
    if not has_session_cookie:
        raise ValueError("身份 Cookie 缺少会话标记")

    user_agent = bundle.user_agent
    try:
        user_agent_size = len(user_agent.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("User-Agent 不是有效的 UTF-8 文本") from None
    if user_agent_size > 2 * 1024:
        raise ValueError("User-Agent 超过长度限制")
    if "\r" in user_agent or "\n" in user_agent:
        raise ValueError("User-Agent 不能包含换行符")


def browser_cookies_from_identity(bundle: IdentityBundle) -> list[dict]:
    """把已验证 HTTP Cookie 限域映射为一次性浏览器 bootstrap Cookie。"""
    validate_identity(bundle)
    pairs: list[tuple[str, str]] = []
    for raw_part in bundle.cookie.split(";"):
        name, separator, value = raw_part.strip().partition("=")
        if not separator or not name or not value or name.startswith("__Host-"):
            continue
        if not _COOKIE_NAME_RE.fullmatch(name):
            raise ValueError("身份 Cookie 名称无效")
        if any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        ):
            raise ValueError("身份 Cookie 内容无效")
        pairs.append((name, value))
    if not pairs:
        raise ValueError("身份 Cookie 无法构造浏览器会话")
    return [
        {
            "name": name,
            "value": value,
            "domain": domain,
            "path": "/",
            "secure": True,
            "sameSite": "Lax",
        }
        for domain in (".tiktok.com", ".tiktokshop.com")
        for name, value in pairs
    ]


def load_identity(path: Path) -> IdentityBundle:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("身份文件根节点必须是对象")

    if "meta" in payload:
        meta_payload = payload["meta"]
        try:
            if (
                not isinstance(meta_payload, dict)
                or set(meta_payload) != _IDENTITY_META_FIELDS
            ):
                raise ValueError
            _validate_meta(IdentityMeta(**meta_payload))
        except (TypeError, ValueError):
            raise ValueError("身份文件元数据无效") from None

    headers_payload = payload.get("headers", payload)
    if not isinstance(headers_payload, dict):
        raise ValueError("身份文件的请求头必须是对象")

    headers = dict(headers_payload)
    bundle = IdentityBundle(headers=headers)
    validate_identity(bundle)
    return bundle


def _path_lstat_without_links(path: Path) -> os.stat_result:
    target = Path(path)
    info = target.lstat()
    file_attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    if stat.S_ISLNK(info.st_mode) or file_attributes & _WINDOWS_REPARSE_POINT:
        raise OSError(errno.ELOOP, "敏感路径不允许符号链接或重解析点", target)
    return info


def _require_regular_file(path: Path) -> os.stat_result:
    target = Path(path)
    info = _path_lstat_without_links(target)
    if not stat.S_ISREG(info.st_mode):
        raise OSError(errno.EINVAL, "敏感文件路径不是普通文件", target)
    return info


def _require_directory(path: Path) -> os.stat_result:
    target = Path(path)
    info = _path_lstat_without_links(target)
    if not stat.S_ISDIR(info.st_mode):
        raise NotADirectoryError(errno.ENOTDIR, "敏感目录路径不是目录", target)
    return info


def _regular_file_exists(path: Path) -> bool:
    try:
        _require_regular_file(path)
    except FileNotFoundError:
        return False
    return True


def _harden_posix(path: Path, *, directory: bool, mode: int) -> None:
    """从同一个 no-follow 文件描述符校验类型并收紧权限。"""
    target = Path(path)
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | (getattr(os, "O_DIRECTORY", 0) if directory else 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    before = _path_lstat_without_links(target)
    descriptor = os.open(target, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not expected(opened.st_mode)
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            raise OSError(errno.EINVAL, "敏感路径在权限加固前发生变化", target)
        os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)


def harden_permissions(path: Path) -> None:
    target = Path(path)
    if os.name != "nt":
        _harden_posix(
            target,
            directory=False,
            mode=stat.S_IRUSR | stat.S_IWUSR,
        )
        return

    _require_regular_file(target)
    _harden_windows_acl(target, directory=False)


def harden_directory_permissions(path: Path) -> None:
    target = Path(path)
    if os.name != "nt":
        _harden_posix(target, directory=True, mode=stat.S_IRWXU)
        return

    _require_directory(target)
    _harden_windows_acl(target, directory=True)


def _harden_windows_acl(target: Path, *, directory: bool) -> None:
    encoded_target = base64.b64encode(
        str(target).encode("utf-8")
    ).decode("ascii")
    target_kind = "directory" if directory else "file"
    powershell = _WINDOWS_PRIVATE_ACL_SCRIPT.replace(
        "__BDHUB_TARGET_TOKEN__",
        encoded_target,
    ).replace(
        "__BDHUB_TARGET_KIND__",
        target_kind,
    )
    encoded_command = base64.b64encode(
        powershell.encode("utf-16le")
    ).decode("ascii")
    subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded_command,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="mbcs",
        errors="replace",
    )


def ensure_private_directory(path: Path) -> Path:
    """创建或收紧只允许当前用户访问的敏感目录。"""
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True, mode=stat.S_IRWXU)
    _require_directory(target)
    harden_directory_permissions(target)
    return target


def write_private_bytes_exclusive(
    path: Path,
    payload: bytes,
    *,
    harden: Callable[[Path], None] = harden_permissions,
) -> None:
    """以 0600 独占创建完整文件；失败时不保留部分敏感内容。"""
    target = Path(path)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(target, flags, stat.S_IRUSR | stat.S_IWUSR)
    stream = None
    try:
        # Windows 的 mode 参数不等价于 ACL；必须在写入敏感内容前加固空文件。
        harden(target)
        stream = os.fdopen(descriptor, "wb")
        descriptor = -1
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
        stream.close()
        stream = None
    except BaseException:
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        target.unlink(missing_ok=True)
        raise


def write_private_text_exclusive(
    path: Path,
    text: str,
    *,
    harden: Callable[[Path], None] = harden_permissions,
) -> None:
    write_private_bytes_exclusive(
        path,
        text.encode("utf-8"),
        harden=harden,
    )


def _browser_session_path(
    profile_dir: Path,
    account: str,
    market: str,
) -> Path:
    validate_account_name(account)
    if not isinstance(market, str) or market not in MARKETS:
        raise ValueError("浏览器会话市场不受支持")
    root = Path(profile_dir).resolve()
    # 只解析受信的 profile 根目录，保留末级文件本身，后续才能拒绝预置链接。
    candidate = root / _BROWSER_SESSION_FILE.format(market=market)
    if not candidate.is_relative_to(root):
        raise ValueError("浏览器会话路径超出 profile 目录")
    return candidate


def _normalize_browser_cookies(cookies) -> list[dict]:
    if not isinstance(cookies, (list, tuple)) or not cookies:
        raise ValueError("浏览器 Cookie 必须是非空列表")
    normalized = []
    has_session_cookie = False
    for raw in cookies:
        if not isinstance(raw, dict):
            raise ValueError("浏览器 Cookie 项必须是对象")
        name = raw.get("name")
        value = raw.get("value")
        domain = raw.get("domain")
        path = raw.get("path", "/")
        if not isinstance(name, str) or not _COOKIE_NAME_RE.fullmatch(name):
            raise ValueError("浏览器 Cookie 名称无效")
        if not isinstance(value, str) or any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        ):
            raise ValueError("浏览器 Cookie 内容无效")
        if not isinstance(domain, str):
            raise ValueError("浏览器 Cookie domain 无效")
        root_domain = domain.strip().casefold().lstrip(".")
        if not any(
            root_domain == root or root_domain.endswith(f".{root}")
            for root in _TIKTOK_COOKIE_ROOTS
        ):
            raise ValueError("浏览器 Cookie domain 不受支持")
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or "\r" in path
            or "\n" in path
        ):
            raise ValueError("浏览器 Cookie path 无效")

        expires = raw.get("expires", -1)
        if (
            type(expires) not in {int, float}
            or not math.isfinite(float(expires))
            or float(expires) < -1
        ):
            raise ValueError("浏览器 Cookie expires 无效")
        http_only = raw.get("httpOnly", False)
        secure = raw.get("secure", False)
        same_site = raw.get("sameSite", "Lax")
        if type(http_only) is not bool or type(secure) is not bool:
            raise ValueError("浏览器 Cookie 安全属性无效")
        if same_site not in _COOKIE_SAME_SITE:
            raise ValueError("浏览器 Cookie sameSite 无效")

        if name in _SESSION_COOKIE_NAMES and value:
            has_session_cookie = True
        normalized.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                "expires": expires,
                "httpOnly": http_only,
                "secure": secure,
                "sameSite": same_site,
            }
        )
    if not has_session_cookie:
        raise ValueError("浏览器 Cookie 缺少会话标记")
    return normalized


def load_verified_browser_cookies(
    profile_dir: Path,
    account: str,
    market: str,
) -> list[dict] | None:
    path = _browser_session_path(profile_dir, account, market)
    if not _regular_file_exists(path):
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "account",
        "market",
        "cookies",
    }:
        raise ValueError("浏览器会话文件结构无效")
    if (
        type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or payload.get("account") != account
        or payload.get("market") != market
    ):
        raise ValueError("浏览器会话身份不匹配")
    return _normalize_browser_cookies(payload.get("cookies"))


def write_verified_browser_cookies(
    profile_dir: Path,
    account: str,
    market: str,
    cookies,
    *,
    harden: Callable[[Path], None] = harden_permissions,
) -> Path:
    path = _browser_session_path(profile_dir, account, market)
    normalized = _normalize_browser_cookies(cookies)
    payload = {
        "schema_version": 1,
        "account": account,
        "market": market,
        "cookies": normalized,
    }
    ensure_private_directory(path.parent)
    path_exists = _regular_file_exists(path)
    if path_exists:
        harden(path)
    temp_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        write_private_text_exclusive(
            temp_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            harden=harden,
        )
        json.loads(temp_path.read_text(encoding="utf-8"))
        if path_exists and os.name == "nt":
            path.chmod(path.stat().st_mode | stat.S_IWRITE)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
    return path


def _backup_parts(backup: Path, target_name: str) -> tuple[str, int | None]:
    prefix = f"{target_name}.bak."
    suffix = backup.name.removeprefix(prefix)
    timestamp, separator, raw_sequence = suffix.partition(".")
    if not _BACKUP_TIMESTAMP_RE.fullmatch(timestamp):
        return "", None
    if (
        separator
        and len(raw_sequence) == _BACKUP_SEQUENCE_WIDTH
        and raw_sequence.isascii()
        and raw_sequence.isdecimal()
    ):
        return timestamp, int(raw_sequence)
    return timestamp, None


def _backup_sort_key(
    backup: Path,
    target_name: str,
) -> tuple[str, int, int, str]:
    timestamp, sequence = _backup_parts(backup, target_name)
    if sequence is not None:
        return timestamp, sequence, 0, backup.name
    return timestamp, -1, backup.lstat().st_mtime_ns, backup.name


def _prune_backups(path: Path, keep: int = 3) -> None:
    backups = list(path.parent.glob(f"{path.name}.bak.*"))
    for backup in backups:
        _require_regular_file(backup)
    backups.sort(key=lambda backup: _backup_sort_key(backup, path.name))
    stale_count = max(len(backups) - max(keep, 0), 0)
    for backup in backups[:stale_count]:
        backup.unlink()


def _copy_backup(
    path: Path,
    backup_path: Path,
    *,
    harden: Callable[[Path], None],
) -> None:
    _require_regular_file(path)
    content = path.read_bytes()
    temp_path = backup_path.parent / f".{backup_path.name}.{uuid4().hex}.tmp"
    try:
        write_private_bytes_exclusive(
            temp_path,
            content,
            harden=harden,
        )
        if os.name == "nt":
            # Windows 的 rename 在目标已存在时失败，保留原有竞争重试语义。
            os.rename(temp_path, backup_path)
        else:
            # 硬链接以 no-clobber 方式让完整备份一次可见，避免暴露半写文件。
            os.link(temp_path, backup_path, follow_symlinks=False)
            temp_path.unlink()
    finally:
        temp_path.unlink(missing_ok=True)


def _copy_ordered_backup(
    path: Path,
    timestamp: str,
    *,
    harden: Callable[[Path], None],
) -> Path:
    sequence = -1
    for backup in path.parent.glob(f"{path.name}.bak.{timestamp}.*"):
        _require_regular_file(backup)
        parsed_timestamp, candidate = _backup_parts(backup, path.name)
        if parsed_timestamp == timestamp and candidate is not None:
            sequence = max(sequence, candidate)
    sequence += 1
    max_sequence = (10 ** _BACKUP_SEQUENCE_WIDTH) - 1

    for _attempt in range(_MAX_BACKUP_CREATE_ATTEMPTS):
        if sequence > max_sequence:
            break
        backup_path = path.with_name(
            f"{path.name}.bak.{timestamp}.{sequence:0{_BACKUP_SEQUENCE_WIDTH}d}"
        )
        try:
            _copy_backup(path, backup_path, harden=harden)
        except FileExistsError:
            sequence += 1
            continue
        return backup_path
    raise FileExistsError("身份备份序号竞争重试耗尽")


def write_verified_identity(
    path: Path,
    bundle: IdentityBundle,
    meta: IdentityMeta,
    *,
    harden: Callable[[Path], None] = harden_permissions,
) -> tuple[str, ...]:
    validate_identity(bundle)
    _validate_meta(meta)

    target = Path(path)
    ensure_private_directory(target.parent)
    temp_path = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    warnings: list[str] = []
    payload = {
        "headers": dict(bundle.headers),
        "meta": {
            "schema_version": meta.schema_version,
            "account": meta.account,
            "market": meta.market,
            "verified_at": meta.verified_at,
            "verification_method": meta.verification_method,
        },
    }

    try:
        target_exists = _regular_file_exists(target)
        if target_exists:
            harden(target)
        for backup in target.parent.glob(f"{target.name}.bak.*"):
            _require_regular_file(backup)
            harden(backup)

        write_private_text_exclusive(
            temp_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            harden=harden,
        )

        load_identity(temp_path)
        if target_exists and os.name == "nt":
            target.chmod(target.stat().st_mode | stat.S_IWRITE)

        if target_exists:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            _copy_ordered_backup(target, timestamp, harden=harden)

        os.replace(temp_path, target)
        try:
            _prune_backups(target, keep=3)
        except OSError:
            warnings.append("backup_prune_failed")
    finally:
        temp_path.unlink(missing_ok=True)

    return tuple(warnings)
