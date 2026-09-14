"""Handle 名单的统一安全读取边界。"""
from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

from .hub.keys import norm_handle


MAX_HANDLE_FILE_BYTES = 10 * 1024 * 1024
MAX_HANDLE_FILE_LINES = 50_000
_READ_CHUNK_BYTES = 64 * 1024


class HandleFileError(ValueError):
    """只暴露稳定错误码，避免把本机路径或文件内容带到 API。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _open_error(error: OSError) -> HandleFileError:
    if error.errno == errno.ELOOP:
        return HandleFileError("handle_file_symlink")
    if error.errno == errno.ENOENT:
        return HandleFileError("handle_file_not_found")
    if error.errno in {errno.ENOTDIR, errno.EISDIR}:
        return HandleFileError("handle_file_not_regular")
    if error.errno in {errno.EACCES, errno.EPERM}:
        return HandleFileError("handle_file_unreadable")
    return HandleFileError("handle_file_open_failed")


def _absolute_path(path: str | os.PathLike[str]) -> Path:
    try:
        raw = os.fspath(path)
    except TypeError:
        raise HandleFileError("handle_file_path_invalid") from None
    if not isinstance(raw, (str, bytes)) or not raw:
        raise HandleFileError("handle_file_path_invalid")
    try:
        absolute = Path(os.path.abspath(raw))
        # macOS 的 /tmp 是 /private/tmp 的系统级别名。只解析父目录，末级文件
        # 仍由 O_NOFOLLOW 打开，因此兼容系统别名但不会放行文件符号链接。
        return absolute.parent.resolve(strict=True) / absolute.name
    except (TypeError, ValueError, OSError):
        raise HandleFileError("handle_file_path_invalid") from None


def _open_posix_no_links(path: Path) -> int:
    """逐层 openat + O_NOFOLLOW，目录或末级被替换也不会跟随链接。"""
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = (
        os.O_RDONLY
        | os.O_NONBLOCK
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
    )
    parts = path.parts
    if len(parts) < 2:
        try:
            return os.open(path, file_flags)
        except OSError as error:
            raise _open_error(error) from None

    try:
        directory_fd = os.open(path.anchor, directory_flags)
    except OSError as error:
        raise _open_error(error) from None
    try:
        for component in parts[1:-1]:
            try:
                next_fd = os.open(
                    component,
                    directory_flags,
                    dir_fd=directory_fd,
                )
            except OSError as error:
                raise _open_error(error) from None
            os.close(directory_fd)
            directory_fd = next_fd
        try:
            return os.open(parts[-1], file_flags, dir_fd=directory_fd)
        except OSError as error:
            raise _open_error(error) from None
    finally:
        os.close(directory_fd)


def _open_fallback_no_links(path: Path) -> int:
    """无 openat/O_NOFOLLOW 平台的保守回退：拒绝任一链接并核对 inode。"""
    current = Path(path.anchor)
    final_stat = None
    try:
        for component in path.parts[1:]:
            current /= component
            final_stat = os.lstat(current)
            if stat.S_ISLNK(final_stat.st_mode):
                raise HandleFileError("handle_file_symlink")
    except HandleFileError:
        raise
    except OSError as error:
        raise _open_error(error) from None
    if final_stat is None or not stat.S_ISREG(final_stat.st_mode):
        raise HandleFileError("handle_file_not_regular")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise _open_error(error) from None
    opened_stat = os.fstat(descriptor)
    if (
        opened_stat.st_dev != final_stat.st_dev
        or opened_stat.st_ino != final_stat.st_ino
    ):
        os.close(descriptor)
        raise HandleFileError("handle_file_changed")
    return descriptor


def _open_no_links(path: Path) -> int:
    supports_openat = (
        os.name != "nt"
        and bool(getattr(os, "O_DIRECTORY", 0))
        and bool(getattr(os, "O_NOFOLLOW", 0))
        and os.open in os.supports_dir_fd
    )
    return (
        _open_posix_no_links(path)
        if supports_openat
        else _open_fallback_no_links(path)
    )


def read_bounded_utf8_lines(
    path: str | os.PathLike[str],
    *,
    max_bytes: int = MAX_HANDLE_FILE_BYTES,
    max_lines: int = MAX_HANDLE_FILE_LINES,
) -> list[str]:
    """从同一安全文件描述符完成类型检查与有界 UTF-8 读取。"""
    if (
        type(max_bytes) is not int
        or max_bytes < 1
        or type(max_lines) is not int
        or max_lines < 1
    ):
        raise ValueError("handle_file_limits_invalid")

    descriptor = _open_no_links(_absolute_path(path))
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise HandleFileError("handle_file_not_regular")
        if opened_stat.st_size > max_bytes:
            raise HandleFileError("handle_file_too_large")

        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            try:
                chunk = os.read(
                    descriptor,
                    min(_READ_CHUNK_BYTES, remaining),
                )
            except OSError:
                raise HandleFileError("handle_file_read_failed") from None
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > max_bytes:
            raise HandleFileError("handle_file_too_large")
    finally:
        os.close(descriptor)

    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise HandleFileError("handle_file_invalid_utf8") from None
    if len(lines) > max_lines:
        raise HandleFileError("handle_file_too_many_lines")
    return lines


def read_bounded_handles(
    path: str | os.PathLike[str],
    *,
    max_bytes: int = MAX_HANDLE_FILE_BYTES,
    max_lines: int = MAX_HANDLE_FILE_LINES,
) -> list[str]:
    """按全项目 handle_key 口径读取、去空、去重，并兼容 UTF-8 BOM。"""
    handles = list(dict.fromkeys(
        handle
        for line in read_bounded_utf8_lines(
            path,
            max_bytes=max_bytes,
            max_lines=max_lines,
        )
        if (handle := norm_handle(str(line).lstrip("\ufeff")))
    ))
    if not handles:
        raise HandleFileError("handle_file_no_valid_handles")
    return handles


def validate_leads_handle_file(
    value: object,
    *,
    repo_root: str | os.PathLike[str],
) -> str:
    """验证 Dashboard job 参数并返回规范的仓库相对路径。"""
    if not isinstance(value, str) or not value.strip():
        raise HandleFileError("handles_file_required")
    raw = value.strip()
    relative = Path(raw)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or len(relative.parts) < 3
        or relative.parts[:2] != ("data", "leads")
    ):
        raise HandleFileError("handles_file_outside_data_leads")

    root = _absolute_path(repo_root)
    try:
        leads_root = (root / "data" / "leads").resolve(strict=True)
    except OSError:
        raise HandleFileError("handle_file_not_found") from None
    candidate = root / relative
    try:
        candidate = candidate.parent.resolve(strict=True) / candidate.name
    except OSError:
        raise HandleFileError("handle_file_not_found") from None
    if not candidate.parent.is_relative_to(leads_root):
        raise HandleFileError("handles_file_outside_data_leads")
    read_bounded_handles(candidate)
    return relative.as_posix()


__all__ = [
    "HandleFileError",
    "MAX_HANDLE_FILE_BYTES",
    "MAX_HANDLE_FILE_LINES",
    "read_bounded_handles",
    "read_bounded_utf8_lines",
    "validate_leads_handle_file",
]
