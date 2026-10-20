import re
import subprocess
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

# Apple's macOS licence allows at most two macOS VMs per Mac.
MAX_SLOTS = 2
# Two 8GB VMs don't fit alongside the host on a 16GB Mac.
TWO_SLOT_MEMORY = 24 * 1024**3
# config.sh adds these to every runner; JIT configs only get the labels we send.
DEFAULT_LABELS = ("self-hosted", "macOS", "ARM64")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class GitHub:
    organization: str
    app_id: int
    private_key: Path
    runner_group: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class VM:
    image: str
    insecure: bool
    username: str
    password: str
    cpu: int | None
    memory_gb: int | None


@dataclass(frozen=True)
class Config:
    host: str
    github: GitHub
    vm: VM
    runner_version: str
    slots: int
    max_vm_hours: float


def load(path, *, home=None, host=None, memory=None):
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from e
    return parse(
        data,
        home=Path(home) if home else Path.home(),
        host=host or local_host_name(),
        memory=memory if memory is not None else host_memory(),
    )


def parse(data, *, home, host, memory):
    top = _table(data, "config", required=("github", "vm"), optional=("runner", "host", "hosts"))
    github = _table(
        top["github"], "github",
        required=("organization", "app_id", "private_key", "labels"),
        optional=("runner_group",),
    )
    vm = _table(
        top["vm"], "vm",
        required=("image",),
        optional=("insecure", "username", "password", "cpu", "memory_gb"),
    )
    runner = _table(top.get("runner", {}), "runner", optional=("version",))
    host_defaults = _table(top.get("host", {}), "host", optional=("slots", "max_vm_hours"))

    overrides = {}
    for name, table in _get(top, "hosts", dict, {}).items():
        overrides[name.lower()] = _table(table, f"hosts.{name}", optional=("labels", "slots"))
    override = overrides.get(host.lower(), {})

    labels = _labels(override.get("labels", github["labels"]), "labels")
    version = _get(runner, "version", str, "latest", "runner.version")
    if version != "latest" and not re.fullmatch(r"\d+(\.\d+)+", version):
        raise ConfigError(f'runner.version must be "latest" or a version like "2.337.0", not {version!r}')

    config = Config(
        host=host,
        github=GitHub(
            organization=_get(github, "organization", str, where="github.organization"),
            app_id=_get(github, "app_id", int, where="github.app_id"),
            private_key=_expand(_get(github, "private_key", str, where="github.private_key"), home),
            runner_group=_get(github, "runner_group", str, "Default", "github.runner_group"),
            labels=labels,
        ),
        vm=VM(
            image=_get(vm, "image", str, where="vm.image"),
            insecure=_get(vm, "insecure", bool, False, "vm.insecure"),
            username=_get(vm, "username", str, "admin", "vm.username"),
            password=_get(vm, "password", str, "admin", "vm.password"),
            cpu=_get(vm, "cpu", int, None, "vm.cpu"),
            memory_gb=_get(vm, "memory_gb", int, None, "vm.memory_gb"),
        ),
        runner_version=version,
        slots=0,
        max_vm_hours=float(_get(host_defaults, "max_vm_hours", (int, float), 24, "host.max_vm_hours")),
    )
    slots = override.get("slots", host_defaults.get("slots", "auto"))
    return replace(config, slots=_slots(slots, memory))


def local_host_name():
    result = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        raise ConfigError(f"can't read LocalHostName: {result.stderr.strip()}")
    return result.stdout.strip()


def host_memory():
    return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True).stdout)


def _slots(value, memory):
    if value == "auto":
        return MAX_SLOTS if memory >= TWO_SLOT_MEMORY else 1
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_SLOTS:
        raise ConfigError(f'slots must be "auto" or 0 to {MAX_SLOTS}, not {value!r}')
    return value


def _labels(value, where):
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        raise ConfigError(f"{where} must be a non-empty list of strings")
    return tuple(dict.fromkeys(DEFAULT_LABELS + tuple(value)))


def _expand(path, home):
    if path == "~" or path.startswith("~/"):
        return home / path[2:]
    return Path(path)


def _table(value, where, required=(), optional=()):
    if not isinstance(value, dict):
        raise ConfigError(f"{where} must be a table")
    missing = [k for k in required if k not in value]
    if missing:
        raise ConfigError(f"{where} is missing {', '.join(missing)}")
    unknown = sorted(set(value) - set(required) - set(optional))
    if unknown:
        raise ConfigError(f"{where} has unknown keys: {', '.join(unknown)}")
    return value


_MISSING = object()


def _get(table, key, kind, default=_MISSING, where=None):
    if key not in table:
        if default is _MISSING:
            raise ConfigError(f"{where or key} is required")
        return default
    value = table[key]
    # TOML booleans are ints in Python, so an int field must reject them explicitly.
    if not isinstance(value, kind) or (isinstance(value, bool) and kind is not bool):
        raise ConfigError(f"{where or key} has the wrong type: {value!r}")
    return value
