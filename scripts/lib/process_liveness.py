"""Decide whether a recorded worker pid is still that worker, for local supervision.

Exited-but-unreaped children count as dead.  A pid also outlives its process: after the worker exits
(or the machine restarts) the number is handed to an unrelated process, and the record would keep that
stranger "alive" so nobody relaunches the worker.  The worker started before it (or its launcher)
wrote the record, while any later owner of the number started after the record's last write, so a
process that started after ``recorded_at`` is not the recorded one.
"""
import os
import subprocess
import time

# What a status record carries about when it was written; launchers write startedAt, workers checkedAt.
RECORD_TIMES = ('checkedAt', 'startedAt')
# ps reports start times truncated to the second.
START_RESOLUTION_SECONDS = 1


def _running_state(value):
    return bool(value and value.strip()) and not value.strip().upper().startswith('Z')


def _started_at(lstart):
    try:
        return time.mktime(time.strptime(' '.join(lstart.split()), '%a %b %d %H:%M:%S %Y'))
    except (ValueError, OverflowError):
        return None


def pid_alive(pid, recorded_at=None):
    """True while ``pid`` runs and, when ``recorded_at`` is given, started no later than that record."""
    if type(pid) is not int or pid<=0:return False
    try:os.kill(pid,0)
    except ProcessLookupError:return False
    except PermissionError:pass
    except OSError:return False
    if os.name!='posix':return True
    try:
        result=subprocess.run(['ps','-p',str(pid),'-o','stat=,lstart='],capture_output=True,text=True,timeout=3,
                              env={**os.environ,'LC_ALL':'C'})
    except (OSError,subprocess.TimeoutExpired):
        return True
    if result.returncode!=0 or not _running_state(result.stdout):return False
    if type(recorded_at) not in (int,float):return True
    parts=result.stdout.split(None,1)
    started=_started_at(parts[1]) if len(parts)==2 else None
    # An unreadable start time keeps the old answer rather than relaunching a live worker.
    return started is None or started<=recorded_at+START_RESOLUTION_SECONDS


def record_alive(value):
    """``pid_alive`` for a status record: its pid, judged against the latest write time it carries."""
    if not isinstance(value,dict):return False
    stamps=[value[key] for key in RECORD_TIMES if type(value.get(key)) in (int,float)]
    return pid_alive(value.get('pid'),max(stamps) if stamps else None)
