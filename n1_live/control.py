"""Persistent emergency latch. Stops processing, never fabricates a liquidation."""
import json
import os
import tempfile
import time
from pathlib import Path

class EmergencyStop(RuntimeError):
    pass


def halt_path(database):
    return Path(database).with_suffix('.halt.json')


def emergency_stop(database):
    path=halt_path(database)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.halt-')
    try:
        with os.fdopen(fd,'w') as file:
            json.dump(dict(mode='PAPER_ONLY',requested_at=int(time.time()*1000)),file)
            file.flush();os.fsync(file.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)
    return path


def halted(database):
    return halt_path(database).exists()


def clear_emergency(database):
    halt_path(database).unlink(missing_ok=True)
