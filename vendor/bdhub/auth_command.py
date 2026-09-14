"""有界身份维护子进程；超时只清理本次创建的进程组，不触碰其它Profile。"""
import os
import signal
import subprocess
from pathlib import Path


def run_auth_command(
    argv: list[str], *, cwd: Path, timeout: float,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        argv, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=os.name != "nt",
    )

    def terminate(force: bool = False) -> None:
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            elif force:
                process.kill()
            else:
                process.terminate()
        except ProcessLookupError:
            pass

    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    except BaseException:
        terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            terminate(force=True)
            process.communicate(timeout=5)
        raise
