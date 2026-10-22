import fcntl
import json
import logging
import os
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .github import GitHub
from .guest import Guest, GuestError
from .releases import RunnerReleases, sha256
from .tart import Tart

log = logging.getLogger(__name__)

BACKOFF = (10, 30, 60, 120, 300)
BOOT_TIMEOUT = 180
POLL = 5


def vm_name(slot):
    return f"tart-runners-{slot}"


def runner_name(host, slot):
    return f"{host.lower()}-{slot}-{secrets.token_hex(3)}"


def read_status(home, slot):
    try:
        return json.loads((paths.state_dir(home) / f"slot-{slot}.json").read_text())
    except (OSError, ValueError):
        return None


class Slot:
    def __init__(self, config, config_path, slot, home, tart=None, github=None):
        self.config = config
        self.config_path = Path(config_path)
        self.slot = slot
        self.vm = vm_name(slot)
        self.state = paths.state_dir(home)
        self.logs = paths.log_dir(home)
        self.tart = tart or Tart()
        self.github = github or GitHub(config.github.organization, config.github.app_id, config.github.private_key)
        self.releases = RunnerReleases(self.state / "runner-cache", config.runner_version, self.github)
        self._runner_group_id = None
        self._started_from = paths.current_release()
        self._config_digest = sha256(self.config_path)

    def run_forever(self):
        self.state.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        self._remove_vm()
        failures = 0
        while not self._outdated():
            if self.slot > self.config.slots:
                self._status("disabled", f"host has {self.config.slots} slots")
                time.sleep(60)
                continue
            try:
                self.run_once()
                failures = 0
            except Exception as e:
                delay = BACKOFF[min(failures, len(BACKOFF) - 1)]
                failures += 1
                log.error("slot %s failed, retrying in %ss: %s", self.slot, delay, e)
                self._status("failing", str(e))
                time.sleep(delay)
        log.info("code or config changed, exiting to be restarted")

    def run_once(self):
        # Checked before booting so a bad key doesn't cycle VMs that can never register.
        self.github.token()
        group_id = self._group_id()
        tarball = self.releases.tarball()

        self._remove_vm()
        # The first clone pulls the image, which can take an hour; the other slot waits for it.
        self._status("cloning", self.config.vm.image)
        with self._lock("clone"):
            self.tart.clone(self.config.vm.image, self.vm, self.config.vm.insecure)
        self.tart.configure(self.vm, self.config.vm.cpu, self.config.vm.memory_gb)
        log.debug("cloned %s with MAC %s", self.vm, self.tart.mac(self.vm))

        self._status("booting")
        runner_id = None
        with open(self.logs / f"vm-{self.slot}.log", "ab") as vm_log:
            vm = self.tart.run(self.vm, vm_log)
            try:
                ip = self.tart.ip(self.vm, BOOT_TIMEOUT)
                log.debug("%s has IP %s", self.vm, ip)
                guest = Guest(ip, self.config.vm.username, self.config.vm.password)
                guest.wait_until_reachable(BOOT_TIMEOUT, vm)
                log.debug("SSH to %s is up", ip)
                guest.install_runner(tarball)
                log.debug("installed %s", tarball.name)
                name = runner_name(self.config.host, self.slot)
                runner_id, jit_config = self.github.jit_config(name, self.config.github.labels, group_id)
                log.info("registered runner %s", name)
                self._status("ready", name)
                self._wait(guest.start_runner(jit_config, vm_log), vm)
            finally:
                if runner_id is not None:
                    try:
                        self.github.delete_runner(runner_id)
                    except Exception as e:
                        log.warning("can't deregister runner %s: %s", runner_id, e)
                self._stop(vm)

    def _wait(self, runner, vm):
        deadline = time.monotonic() + self.config.max_vm_hours * 3600
        while runner.poll() is None:
            if vm.poll() is not None:
                runner.terminate()
                raise GuestError(f"VM exited with {vm.returncode} while the runner was running")
            if time.monotonic() > deadline:
                log.warning("VM up for %s hours, tearing it down", self.config.max_vm_hours)
                runner.terminate()
                return
            time.sleep(POLL)
        if runner.returncode != 0:
            raise GuestError(f"runner exited with {runner.returncode}")
        log.info("runner finished")

    def _stop(self, vm):
        try:
            self.tart.stop(self.vm)
        except Exception as e:
            log.warning("can't stop %s: %s", self.vm, e)
        try:
            vm.wait(timeout=60)
        except Exception:
            vm.kill()
            vm.wait()
        self._remove_vm()

    def _remove_vm(self):
        if self.tart.exists(self.vm):
            try:
                self.tart.stop(self.vm)
            except Exception:
                pass
            self.tart.delete(self.vm)

    def _group_id(self):
        if self._runner_group_id is None:
            self._runner_group_id = self.github.runner_group_id(self.config.github.runner_group)
        return self._runner_group_id

    def _outdated(self):
        try:
            config_digest = sha256(self.config_path)
        except OSError:
            return True
        return paths.current_release() != self._started_from or config_digest != self._config_digest

    def _status(self, state, detail=""):
        path = self.state / f"slot-{self.slot}.json"
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps({
            "state": state,
            "detail": detail,
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }))
        os.replace(tmp, path)

    @contextmanager
    def _lock(self, name):
        with open(self.state / f"{name}.lock", "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield
