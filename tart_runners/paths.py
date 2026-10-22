from pathlib import Path

ROOT = Path("/Library/Application Support/tart-runners")
CONFIG = ROOT / "config.toml"
RELEASES = ROOT / "releases"
CURRENT = ROOT / "current"


def state_dir(home):
    return Path(home) / "Library/Application Support/tart-runners"


def log_dir(home):
    return Path(home) / "Library/Logs/tart-runners"


def disabled_marker(home):
    return state_dir(home) / "disabled"


def disabled(home):
    """Why this host was disabled, or None when it's taking jobs."""
    try:
        return disabled_marker(home).read_text().strip()
    except FileNotFoundError:
        return None


def current_release():
    return CURRENT.resolve() if CURRENT.exists() else None
