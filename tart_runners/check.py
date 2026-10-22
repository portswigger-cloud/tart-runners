import subprocess
import time

from . import paths
from .github import GitHub
from .slot import read_status


def daemon_label(slot):
    return f"tart-runners.slot-{slot}"


def daemon_loaded(slot):
    return subprocess.run(["launchctl", "print", f"system/{daemon_label(slot)}"], capture_output=True).returncode == 0


def check(config, home, services=False, wait=0, github=None, sleep=time.sleep):
    """Return the reasons this host isn't serving its runners; empty when it is."""
    reason = paths.disabled(home)
    if reason is not None:
        return [f"disabled {reason}; `tart-runners enable` brings it back", *_slot_states(home, config.slots)]
    github = github or GitHub(config.github.organization, config.github.app_id, config.github.private_key)
    try:
        github.token()
    except Exception as e:
        return [f"can't authenticate as the GitHub App: {e}"]
    if services:
        unloaded = [s for s in range(1, config.slots + 1) if not daemon_loaded(s)]
        if unloaded:
            return [f"slot {s} launch daemon isn't loaded" for s in unloaded]

    deadline = time.monotonic() + wait
    while True:
        online = _online_runners(github, config.host)
        if len(online) >= config.slots:
            return []
        if time.monotonic() >= deadline:
            break
        sleep(30)

    return [f"{len(online)} of {config.slots} runners online for {config.host}", *_slot_states(home, config.slots)]


def _slot_states(home, slots):
    states = []
    for slot in range(1, slots + 1):
        status = read_status(home, slot)
        if status:
            detail = f": {status['detail']}" if status["detail"] else ""
            states.append(f"slot {slot}: {status['state']}{detail} (at {status['updated']})")
        else:
            states.append(f"slot {slot}: hasn't started")
    return states


def _online_runners(github, host):
    prefix = f"{host.lower()}-"
    return [r["name"] for r in github.runners() if r["name"].startswith(prefix) and r["status"] == "online"]
