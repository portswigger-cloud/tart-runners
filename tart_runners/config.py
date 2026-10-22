import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

# Apple's macOS licence allows at most two macOS VMs per Mac.
MAX_SLOTS = 2
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
    cpu: int
    memory_gb: int


@dataclass(frozen=True)
class Machine:
    cores: int
    memory_gb: int
    slots: int
    cpu: int
    vm_memory_gb: int
    labels: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    host: str
    github: GitHub
    vm: VM
    runner_version: str
    slots: int
    max_vm_hours: float


def load(path, *, home=None, host=None, hardware=None):
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from e
    return parse(
        data,
        home=Path(home) if home else Path.home(),
        host=host or local_host_name(),
        hardware=hardware or host_hardware(),
    )


def parse(data, *, home, host, hardware):
    top = _table(data, "config", required=("github", "vm", "machines"), optional=("runner", "host"))
    github = _table(
        top["github"], "github",
        required=("organization", "app_id", "private_key", "labels"),
        optional=("runner_group",),
    )
    vm = _table(
        top["vm"], "vm",
        required=("image",),
        optional=("insecure", "username", "password"),
    )
    runner = _table(top.get("runner", {}), "runner", optional=("version",))
    host_table = _table(top.get("host", {}), "host", optional=("max_vm_hours",))
    machine = _machine(_get(top, "machines", dict), hardware)

    labels = tuple(dict.fromkeys(_labels(github["labels"], "github.labels") + machine.labels))
    version = _get(runner, "version", str, "latest", "runner.version")
    if version != "latest" and not re.fullmatch(r"\d+(\.\d+)+", version):
        raise ConfigError(f'runner.version must be "latest" or a version like "2.337.0", not {version!r}')

    return Config(
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
            cpu=machine.cpu,
            memory_gb=machine.vm_memory_gb,
        ),
        runner_version=version,
        slots=machine.slots,
        max_vm_hours=float(_get(host_table, "max_vm_hours", (int, float), 24, "host.max_vm_hours")),
    )


def local_host_name():
    result = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        raise ConfigError(f"can't read LocalHostName: {result.stderr.strip()}")
    return result.stdout.strip()


def host_hardware():
    """The (cores, memory in GB) that pick this Mac's [machines] entry."""
    values = subprocess.run(
        ["sysctl", "-n", "hw.ncpu", "hw.memsize"], capture_output=True, text=True, check=True
    ).stdout.split()
    return int(values[0]), int(values[1]) // 1024**3


def _machine(machines, hardware):
    cores, memory_gb = hardware
    matches = []
    for name, table in machines.items():
        where = f"machines.{name}"
        _table(table, where, required=("cores", "memory_gb", "slots", "vm"), optional=("labels",))
        vm = _table(table["vm"], f"{where}.vm", required=("cpu", "memory_gb"))
        machine = Machine(
            cores=_positive(table, "cores", where),
            memory_gb=_positive(table, "memory_gb", where),
            slots=_get(table, "slots", int, where=f"{where}.slots"),
            cpu=_positive(vm, "cpu", f"{where}.vm"),
            vm_memory_gb=_positive(vm, "memory_gb", f"{where}.vm"),
            labels=tuple(_label_list(table["labels"], f"{where}.labels")) if "labels" in table else (),
        )
        if not 0 <= machine.slots <= MAX_SLOTS:
            raise ConfigError(f"{where}.slots must be 0 to {MAX_SLOTS}, not {machine.slots}")
        if machine.slots * machine.vm_memory_gb >= machine.memory_gb:
            raise ConfigError(f"{where}: {machine.slots} VMs of {machine.vm_memory_gb}GB leave the host no memory")
        if (machine.cores, machine.memory_gb) == (cores, memory_gb):
            matches.append(machine)
    if not matches:
        raise ConfigError(f"no [machines] entry for a Mac with {cores} cores and {memory_gb}GB")
    if len(matches) > 1:
        raise ConfigError(f"more than one [machines] entry for a Mac with {cores} cores and {memory_gb}GB")
    return matches[0]


def _positive(table, key, where):
    value = _get(table, key, int, where=f"{where}.{key}")
    if value < 1:
        raise ConfigError(f"{where}.{key} must be positive, not {value}")
    return value


def _labels(value, where):
    if not value:
        raise ConfigError(f"{where} must be a non-empty list of strings")
    return tuple(dict.fromkeys(DEFAULT_LABELS + tuple(_label_list(value, where))))


def _label_list(value, where):
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ConfigError(f"{where} must be a list of strings")
    return value


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
