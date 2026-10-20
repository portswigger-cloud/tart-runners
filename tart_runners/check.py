import subprocess
import time

from .github import GitHub
from .slot import read_status


def agent_label(slot):
    return f"tart-runners.slot-{slot}"


def agent_loaded(uid, slot):
    return subprocess.run(
        ["launchctl", "print", f"gui/{uid}/{agent_label(slot)}"], capture_output=True
    ).returncode == 0


def check(config, home, uid=None, wait=0, github=None, sleep=time.sleep):
    """Return the reasons this host isn't serving its runners; empty when it is."""
    github = github or GitHub(config.github.organization, config.github.app_id, config.github.private_key)
    try:
        github.token()
    except Exception as e:
        return [f"can't authenticate as the GitHub App: {e}"]
    if uid is not None:
        unloaded = [s for s in range(1, config.slots + 1) if not agent_loaded(uid, s)]
        if unloaded:
            return [f"slot {s} launch agent isn't loaded" for s in unloaded]

    deadline = time.monotonic() + wait
    while True:
        online = _online_runners(github, config.host)
        if len(online) >= config.slots:
            return []
        if time.monotonic() >= deadline:
            break
        sleep(30)

    problems = [f"{len(online)} of {config.slots} runners online for {config.host}"]
    for slot in range(1, config.slots + 1):
        status = read_status(home, slot)
        if status:
            detail = f": {status['detail']}" if status["detail"] else ""
            problems.append(f"slot {slot}: {status['state']}{detail} (at {status['updated']})")
        else:
            problems.append(f"slot {slot}: hasn't started")
    return problems


def _online_runners(github, host):
    prefix = f"{host.lower()}-"
    return [r["name"] for r in github.runners() if r["name"].startswith(prefix) and r["status"] == "online"]
