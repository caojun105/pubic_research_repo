"""Supervise hourly public-data captures with a fixed deadline and disk reserve."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

GIB = 1024 ** 3
STOP = False


def now_iso(epoch=None):
    return datetime.fromtimestamp(time.time() if epoch is None else epoch, timezone.utc).isoformat()


def reserve_bytes(total):
    return max(10 * GIB, int(total * 0.20))


def request_stop(*_):
    global STOP
    STOP = True


def main(root):
    root.mkdir(parents=True, exist_ok=True)
    lock = (root.parent / 'capture_72h.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, request_stop)
    config_path = root / 'run.json'
    if config_path.exists():
        config = json.loads(config_path.read_text())
    else:
        started = time.time()
        config = {'started_at': now_iso(started), 'deadline_epoch': started + 72 * 3600,
                  'deadline_utc': now_iso(started + 72 * 3600), 'duration_hours': 72,
                  'reserve_min_gib': 10, 'reserve_fraction': 0.20, 'disk_check_seconds': 1,
                  'chunk_seconds': 3600, 'python': sys.executable}
        config_path.write_text(json.dumps(config, indent=2) + '\n')
    deadline = config['deadline_epoch']
    child = None
    state = 'starting'
    last_chunk = None
    last_exit = None

    def status():
        usage = shutil.disk_usage(root)
        data = {'state': state, 'updated_at': now_iso(), 'pid': os.getpid(),
                'deadline_utc': config['deadline_utc'], 'free_gib': usage.free / GIB,
                'reserve_gib': reserve_bytes(usage.total) / GIB,
                'chunk': last_chunk, 'child_pid': child.pid if child else None,
                'last_exit_code': last_exit}
        temp = root / 'supervisor_status.tmp'
        temp.write_text(json.dumps(data, indent=2) + '\n')
        temp.replace(root / 'supervisor_status.json')

    def stopping_reason():
        usage = shutil.disk_usage(root)
        if usage.free <= reserve_bytes(usage.total):
            return 'stopped_low_disk'
        if STOP:
            return 'stopped_by_signal'
        if time.time() >= deadline:
            return 'finished_deadline'
        return None

    def stop_child():
        if child and child.poll() is None:
            child.send_signal(signal.SIGINT)
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()

    try:
        while True:
            reason = stopping_reason()
            if reason:
                state = reason
                break
            remaining = int(deadline - time.time())
            if remaining < 1:
                state = 'finished_deadline'
                break
            last_chunk = 'chunk_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
            with (root / (last_chunk + '.log')).open('a') as log:
                child = subprocess.Popen([sys.executable, '-u', str(Path(__file__).with_name('collect_full_var.py')),
                    '--seconds', str(min(3600, remaining)), '--out', str(root / last_chunk)],
                    stdout=log, stderr=subprocess.STDOUT)
                state = 'running'
                status()
                tick = 0
                while child.poll() is None:
                    reason = stopping_reason()
                    if reason:
                        state = reason
                        stop_child()
                        break
                    if tick % 5 == 0:
                        status()
                    tick += 1
                    time.sleep(1)
                last_exit = child.returncode
                child = None
                status()
                if reason:
                    break
            if last_exit:
                state = 'retry_wait'
                status()
                for _ in range(30):
                    if stopping_reason():
                        break
                    time.sleep(1)
    finally:
        stop_child()
        status()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    main(args.out.resolve())
