"""Treat exited-but-unreaped child processes as dead for local worker supervision."""
import os
import subprocess


def _running_state(value):
    return bool(value and value.strip()) and not value.strip().upper().startswith('Z')


def pid_alive(pid):
    if type(pid) is not int or pid<=0:return False
    try:os.kill(pid,0)
    except ProcessLookupError:return False
    except PermissionError:return True
    except OSError:return False
    if os.name!='posix':return True
    try:
        result=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True,timeout=3)
        return result.returncode==0 and _running_state(result.stdout)
    except (OSError,subprocess.TimeoutExpired):
        return True
