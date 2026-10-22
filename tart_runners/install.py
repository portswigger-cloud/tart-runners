import logging
import os
import plistlib
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

from . import config as configuration
from . import paths
from .check import check, daemon_label, daemon_loaded
from .config import MAX_SLOTS

log = logging.getLogger(__name__)

KEEP_RELEASES = 3
PATH = "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
COMMAND = Path("/usr/local/bin/tart-runners")
DAEMONS = Path("/Library/LaunchDaemons")


def install(user, config_path, release, wait):
    """Install a release for user and return the problems that keep it from serving runners."""
    account = pwd.getpwnam(user)
    home = Path(account.pw_dir)
    config = configuration.load(config_path, home=home)

    paths.ROOT.mkdir(parents=True, exist_ok=True)
    _write_if_changed(paths.CONFIG, Path(config_path).read_bytes(), 0o644)
    _activate(Path(release))
    COMMAND.parent.mkdir(parents=True, exist_ok=True)
    _write_if_changed(COMMAND, _command().encode(), 0o755)
    _prune_releases()
    _configure_host()

    for directory in (paths.state_dir(home), paths.log_dir(home)):
        directory.mkdir(parents=True, exist_ok=True)
        os.chown(directory, account.pw_uid, account.pw_gid)
    for slot in range(1, MAX_SLOTS + 1):
        _sync_daemon(account, slot, enabled=slot <= config.slots)

    return check(config, home, services=True, wait=wait)


def daemon(slot, account):
    """A launchd daemon running as the user, rather than an agent in their login session.

    Local Network privacy blocks unapproved apps such as Homebrew's Python and Tart from
    reaching the LAN, including the image registry, but doesn't apply to launchd daemons.
    """
    home = Path(account.pw_dir)
    logs = paths.log_dir(home)
    return {
        "Label": daemon_label(slot),
        "UserName": account.pw_name,
        "ProgramArguments": [sys.executable, "-m", "tart_runners", "run", "--slot", str(slot)],
        "EnvironmentVariables": {"PATH": PATH, "PYTHONPATH": str(paths.CURRENT), "HOME": str(home)},
        "WorkingDirectory": str(home),
        "RunAtLoad": True,
        "KeepAlive": True,
        # The default Background type throttles CPU and I/O, which the VMs inherit.
        "ProcessType": "Interactive",
        "ThrottleInterval": 30,
        "StandardOutPath": str(logs / f"slot-{slot}.launchd.log"),
        "StandardErrorPath": str(logs / f"slot-{slot}.launchd.log"),
    }


def _sync_daemon(account, slot, enabled):
    label = daemon_label(slot)
    path = DAEMONS / f"{label}.plist"
    if not enabled:
        if daemon_loaded(slot):
            _launchctl("bootout", f"system/{label}")
        path.unlink(missing_ok=True)
        return
    changed = _write_if_changed(path, plistlib.dumps(daemon(slot, account)), 0o644)
    loaded = daemon_loaded(slot)
    if changed and loaded:
        log.info("reloading slot %s, its launch daemon changed", slot)
        _launchctl("bootout", f"system/{label}")
        loaded = False
    if not loaded:
        _launchctl("bootstrap", "system", str(path))


def _command():
    return f'#!/bin/sh\nPYTHONPATH="{paths.CURRENT}" exec "{sys.executable}" -m tart_runners "$@"\n'


def _activate(release):
    link = paths.ROOT / ".current.tmp"
    link.unlink(missing_ok=True)
    link.symlink_to(release.relative_to(paths.ROOT))
    os.replace(link, paths.CURRENT)


def _prune_releases():
    current = paths.current_release()
    releases = sorted(paths.RELEASES.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for release in releases[KEEP_RELEASES:]:
        if release.resolve() != current:
            shutil.rmtree(release)


def _configure_host():
    subprocess.run(
        ["pmset", "-a", "sleep", "0", "disksleep", "0", "standby", "0", "autorestart", "1", "womp", "1"],
        check=True,
    )
    # The 24-hour default runs out of addresses at about 253 VMs a day.
    subprocess.run(
        [
            "defaults", "write", "/Library/Preferences/SystemConfiguration/com.apple.InternetSharing.default.plist",
            "bootpd", "-dict", "DHCPLeaseTimeSecs", "-int", "600",
        ],
        check=True,
    )


def _launchctl(*args):
    result = subprocess.run(["launchctl", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"launchctl {' '.join(args)}: {result.stderr.strip() or result.returncode}")


def _write_if_changed(path, data, mode, account=None):
    try:
        if path.read_bytes() == data:
            return False
    except OSError:
        pass
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, mode)
    if account:
        os.chown(tmp, account.pw_uid, account.pw_gid)
    os.replace(tmp, path)
    return True
