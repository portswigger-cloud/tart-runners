from pathlib import Path

ROOT = Path("/Library/Application Support/tart-runners")
CONFIG = ROOT / "config.toml"
RELEASES = ROOT / "releases"
CURRENT = ROOT / "current"


def state_dir(home):
    return Path(home) / "Library/Application Support/tart-runners"


def log_dir(home):
    return Path(home) / "Library/Logs/tart-runners"


def current_release():
    return CURRENT.resolve() if CURRENT.exists() else None
