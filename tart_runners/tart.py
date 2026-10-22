import json
import subprocess
from pathlib import Path


class TartError(Exception):
    pass


class Tart:
    def __init__(self, binary="tart"):
        self.binary = binary

    def exists(self, name):
        return any(vm["Name"] == name for vm in json.loads(self._run("list", "--format", "json")))

    def clone(self, image, name, insecure=False):
        self._run("clone", image, name, *(["--insecure"] if insecure else []), timeout=None)

    def configure(self, name, cpu=None, memory_gb=None):
        args = []
        if cpu:
            args += ["--cpu", str(cpu)]
        if memory_gb:
            args += ["--memory", str(memory_gb * 1024)]
        if args:
            self._run("set", name, *args)

    def run(self, name, log):
        return subprocess.Popen(
            [self.binary, "run", "--no-graphics", "--no-audio", name],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    def mac(self, name):
        config = Path.home() / ".tart/vms" / name / "config.json"
        return json.loads(config.read_text()).get("macAddress")

    def ip(self, name, wait):
        address = self._run("ip", name, "--wait", str(wait), timeout=wait + 30).strip()
        if not address:
            raise TartError(f"{name} has no IP address")
        return address

    def stop(self, name):
        self._run("stop", name, "--timeout", "30", timeout=90)

    def delete(self, name):
        self._run("delete", name)

    def _run(self, *args, timeout=120):
        try:
            result = subprocess.run([self.binary, *args], capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise TartError(f"tart {args[0]}: {e}") from e
        if result.returncode != 0:
            raise TartError(f"tart {' '.join(args)}: {result.stderr.strip() or result.stdout.strip()}")
        return result.stdout
