from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import socket
import uuid
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

from ..config import validate_account_name
from ..hub.market_catalog import MARKET_CATALOG


_SCHEMA_VERSION = 1
_LEASE_NAME = ".bdhub.lease"
_MUTEX_NAME = ".bdhub.lease.guard"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_PROFILE_HASH_RE = re.compile(r"^[0-9a-f]{12}$")
_OWNER_FIELDS = {
    "schema_version",
    "token",
    "pid",
    "hostname",
    "account",
    "market",
    "operation",
    "started_at",
    "profile_name",
    "profile_hash",
}


@dataclass(frozen=True, slots=True)
class LeaseOwner:
    schema_version: int
    token: str
    pid: int
    hostname: str
    account: str
    market: str
    operation: str
    started_at: str
    profile_name: str
    profile_hash: str


class ProfileBusyError(RuntimeError):
    """浏览器 profile 已有 owner，调用方不得猜测或强制回收。"""

    def __init__(self, owner: LeaseOwner | None) -> None:
        public_owner = asdict(owner) if isinstance(owner, LeaseOwner) else {}
        public_owner.pop("token", None)
        self.owner = public_owner
        super().__init__("浏览器 profile 当前正忙")


def _validate_operation(operation: object) -> str:
    if (
        not isinstance(operation, str)
        or not operation
        or operation != operation.strip()
        or "\r" in operation
        or "\n" in operation
        or len(operation) > 64
    ):
        raise ValueError("operation 必须是 1-64 位、无首尾空白或换行的字符串")
    return operation


def _validate_market(market: object) -> str:
    if not isinstance(market, str) or market not in MARKET_CATALOG:
        raise ValueError("market 不在支持范围内")
    return market


def _parse_utc_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError("started_at 不是有效的 UTC 时间")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("started_at 不是有效的 UTC 时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("started_at 不是有效的 UTC 时间")
    return value


def _pid_alive(pid: int) -> bool:
    """只读检查 PID；权限不足或未知系统错误均按存活处理。"""
    if type(pid) is not int or pid <= 0:
        return False

    if os.name == "nt":
        process_query_limited_information = 0x1000
        error_access_denied = 5
        error_invalid_parameter = 87
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        open_process.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = open_process(
            process_query_limited_information,
            False,
            pid,
        )
        if handle:
            try:
                return True
            finally:
                close_handle(handle)
        error = ctypes.get_last_error()
        if error == error_invalid_parameter:
            return False
        if error == error_access_denied:
            return True
        return True

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


@contextmanager
def _os_mutex(path: Path) -> Iterator[None]:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    guard = os.fdopen(fd, "r+b", buffering=0)
    locked = False
    primary_error: BaseException | None = None
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(guard.fileno()).st_size == 0:
                guard.seek(0)
                guard.write(b"\0")
                guard.flush()
                os.fsync(guard.fileno())
            guard.seek(0)
            msvcrt.locking(guard.fileno(), msvcrt.LK_LOCK, 1)
            locked = True
        else:
            import fcntl

            fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
            locked = True
        yield
    except BaseException as exc:
        primary_error = exc
    finally:
        cleanup_error: BaseException | None = None
        try:
            if locked and os.name == "nt":
                import msvcrt

                guard.seek(0)
                msvcrt.locking(guard.fileno(), msvcrt.LK_UNLCK, 1)
            elif locked:
                import fcntl

                fcntl.flock(guard.fileno(), fcntl.LOCK_UN)
        except BaseException as exc:
            cleanup_error = exc
        try:
            guard.close()
        except BaseException as exc:
            if cleanup_error is None:
                cleanup_error = exc
        if primary_error is not None:
            raise primary_error
        if cleanup_error is not None:
            raise cleanup_error


def _read_payload(path: Path) -> object:
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def _write_exclusive(path: Path, owner: LeaseOwner) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    stream = None
    try:
        stream = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
        fd = -1
        json.dump(
            asdict(owner),
            stream,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
        stream.close()
        stream = None
    except BaseException:
        if stream is not None:
            stream.close()
        if fd >= 0:
            os.close(fd)
        try:
            path.unlink()
        except OSError:
            pass
        raise


class ProfileLease:
    """以 crash-safe OS mutex 串行管理一个浏览器 profile 的 lease。"""

    __slots__ = (
        "profile_dir",
        "path",
        "mutex_path",
        "account",
        "market",
        "operation",
        "owner",
        "_pid_alive",
        "_profile_name",
        "_profile_hash",
    )

    def __init__(
        self,
        profile_dir: str | os.PathLike[str],
        *,
        account: str,
        market: str,
        operation: str,
        pid_alive: Callable[[int], bool] = _pid_alive,
    ) -> None:
        self.account = validate_account_name(account)
        self.market = _validate_market(market)
        self.operation = _validate_operation(operation)
        self.profile_dir = Path(profile_dir)
        self.path = self.profile_dir / _LEASE_NAME
        self.mutex_path = self.profile_dir / _MUTEX_NAME
        self.owner: LeaseOwner | None = None
        self._pid_alive = pid_alive
        resolved = self.profile_dir.resolve()
        self._profile_name = resolved.name
        self._profile_hash = hashlib.sha256(
            str(resolved).encode("utf-8")
        ).hexdigest()[:12]

    def _new_owner(self) -> LeaseOwner:
        return LeaseOwner(
            schema_version=_SCHEMA_VERSION,
            token=uuid.uuid4().hex,
            pid=os.getpid(),
            hostname=socket.gethostname(),
            account=self.account,
            market=self.market,
            operation=self.operation,
            started_at=datetime.now(timezone.utc).isoformat(),
            profile_name=self._profile_name,
            profile_hash=self._profile_hash,
        )

    def _decode_owner(self, payload: object) -> LeaseOwner:
        if not isinstance(payload, dict) or set(payload) != _OWNER_FIELDS:
            raise ValueError("lease owner 格式无效")
        if type(payload["schema_version"]) is not int or payload[
            "schema_version"
        ] != _SCHEMA_VERSION:
            raise ValueError("lease schema 无效")
        token = payload["token"]
        if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
            raise ValueError("lease token 无效")
        pid = payload["pid"]
        if type(pid) is not int or pid <= 0:
            raise ValueError("lease PID 无效")
        hostname = payload["hostname"]
        if (
            not isinstance(hostname, str)
            or not hostname
            or hostname != hostname.strip()
            or "\r" in hostname
            or "\n" in hostname
            or len(hostname) > 255
        ):
            raise ValueError("lease hostname 无效")
        account = validate_account_name(payload["account"])
        market = _validate_market(payload["market"])
        operation = _validate_operation(payload["operation"])
        started_at = _parse_utc_timestamp(payload["started_at"])
        profile_name = payload["profile_name"]
        if profile_name != self._profile_name:
            raise ValueError("lease profile 标识无效")
        profile_hash = payload["profile_hash"]
        if (
            not isinstance(profile_hash, str)
            or not _PROFILE_HASH_RE.fullmatch(profile_hash)
            or profile_hash != self._profile_hash
        ):
            raise ValueError("lease profile 标识无效")
        return LeaseOwner(
            schema_version=_SCHEMA_VERSION,
            token=token,
            pid=pid,
            hostname=hostname,
            account=account,
            market=market,
            operation=operation,
            started_at=started_at,
            profile_name=profile_name,
            profile_hash=profile_hash,
        )

    def _busy_error(self, payload: object) -> ProfileBusyError:
        try:
            owner = self._decode_owner(payload)
        except (KeyError, TypeError, ValueError):
            owner = None
        return ProfileBusyError(owner)

    def _existing_is_stale_local(self, payload: object) -> bool:
        try:
            owner = self._decode_owner(payload)
        except (KeyError, TypeError, ValueError):
            return False
        if owner.hostname != socket.gethostname():
            return False
        try:
            alive = self._pid_alive(owner.pid)
        except Exception:
            return False
        return alive is False

    def has_active_owner(self) -> bool:
        """只读判断 canonical lease 是否仍由活 owner 持有。

        本机死 PID 属于可回收的 stale lease；畸形、远端主机或无法探测的
        owner 一律 fail-closed，仍按活占用处理。
        """
        try:
            payload = _read_payload(self.path)
        except FileNotFoundError:
            return False
        except (OSError, UnicodeError, json.JSONDecodeError):
            return True
        return not self._existing_is_stale_local(payload)

    def public_active_owner(self) -> dict | None:
        """返回当前有效 owner 的脱敏运行信息，不暴露 lease token 或路径。

        畸形或不可读取的 lease 继续由 ``has_active_owner`` fail-closed；这里
        无法证明 owner 身份时返回 ``None``，调用方不得据此强制回收。
        """
        try:
            payload = _read_payload(self.path)
            owner = self._decode_owner(payload)
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError,
                KeyError, TypeError, ValueError):
            return None
        if self._existing_is_stale_local(payload):
            return None
        if owner.account != self.account:
            return None
        return {
            "account": owner.account,
            "market": owner.market,
            "operation": owner.operation,
            "started_at": owner.started_at,
        }

    def acquire(self) -> LeaseOwner:
        if self.owner is not None:
            raise RuntimeError("当前 ProfileLease 已持有 lease")
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        owner = self._new_owner()
        try:
            with _os_mutex(self.mutex_path):
                try:
                    _write_exclusive(self.path, owner)
                except FileExistsError:
                    try:
                        payload = _read_payload(self.path)
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        raise ProfileBusyError(None) from None
                    if not self._existing_is_stale_local(payload):
                        raise self._busy_error(payload)
                    stale_path = self.path.with_name(
                        f"{_LEASE_NAME}.stale.{uuid.uuid4().hex}"
                    )
                    os.replace(self.path, stale_path)
                    try:
                        _write_exclusive(self.path, owner)
                    except BaseException as create_error:
                        if not self.path.exists():
                            try:
                                os.replace(stale_path, self.path)
                            except OSError as restore_error:
                                raise create_error from restore_error
                        raise
                    self.owner = owner
                    try:
                        stale_path.unlink()
                    except OSError:
                        pass
                else:
                    self.owner = owner
        except BaseException:
            if self.owner is owner:
                try:
                    self.release(owner.token)
                except BaseException:
                    pass
            raise
        return owner

    def release(self, token: str) -> None:
        owner = self.owner
        if not isinstance(token, str):
            return
        if owner is not None and token != owner.token:
            return
        ownership_ended = False
        try:
            with _os_mutex(self.mutex_path):
                try:
                    payload = _read_payload(self.path)
                except FileNotFoundError:
                    ownership_ended = True
                except (
                    OSError,
                    UnicodeError,
                    json.JSONDecodeError,
                ):
                    return
                else:
                    try:
                        current = self._decode_owner(payload)
                    except (KeyError, TypeError, ValueError):
                        return
                    if current.token != token:
                        ownership_ended = True
                    else:
                        try:
                            self.path.unlink()
                        except FileNotFoundError:
                            ownership_ended = True
                        except OSError:
                            return
                        else:
                            ownership_ended = True
        except OSError:
            pass
        finally:
            if ownership_ended and owner is not None and token == owner.token:
                self.owner = None

    def __enter__(self) -> LeaseOwner:
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if self.owner is not None:
            self.release(self.owner.token)
        return False
