"""单机外部操作的文件意图账本，原子替换并刷盘。"""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import fcntl
import json
import os
import re

def now():
    return datetime.now(timezone.utc).isoformat()

def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)

class FileIntentStore:
    def __init__(self, root: Path, *, prefix: str, error_prefix: str):
        self.root = root
        self.prefix = prefix
        self.error_prefix = error_prefix

    @contextmanager
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError(f"{self.error_prefix}_prepare_busy") from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def path(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(rf"{self.prefix}_[a-f0-9]{{32}}", job_id):
            raise ValueError(f"{self.error_prefix}_job_id_invalid")
        return self.root / f"{job_id}.json"

    def read(self, job_id):
        return json.loads(self.path(job_id).read_text())

    def save(self, job):
        job["updated_at"] = now()
        path = self.path(job["job_id"])
        atomic_json(path, job)
        # 网络写入前不仅原子替换，还将文件与目录项刷盘。
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
        directory = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def list(self, market):
        rows = [json.loads(p.read_text()) for p in self.root.glob(f"{self.prefix}_*.json")]
        return sorted((r for r in rows if r["market"] == market), key=lambda r: r["created_at"], reverse=True)

