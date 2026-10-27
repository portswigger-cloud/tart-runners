import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

ASKPASS = Path(__file__).with_name("askpass.sh")

# Guests are fresh clones with new host keys every boot, so there is nothing to pin.
SSH_OPTIONS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
    "-o", "PubkeyAuthentication=no",
    "-o", "PreferredAuthentications=password,keyboard-interactive",
    "-o", "NumberOfPasswordPrompts=1",
    "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=6",
]

RUNNER_TARBALL = "actions-runner.tar.gz"


class GuestError(Exception):
    pass


class Guest:
    def __init__(self, ip, username, password):
        self.ip = ip
        self.username = username
        self._env = {
            **os.environ,
            "SSH_ASKPASS": str(ASKPASS),
            "SSH_ASKPASS_REQUIRE": "force",
            "TART_RUNNERS_VM_PASSWORD": password,
        }

    def run(self, command, stdin=None, timeout=120):
        try:
            result = subprocess.run(
                self._ssh(command),
                stdin=stdin if stdin is not None else subprocess.DEVNULL,
                capture_output=True,
                text=True,
                env=self._env,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise GuestError(f"{command!r} timed out after {timeout}s") from e
        if result.returncode != 0:
            raise GuestError(f"{command!r} exited {result.returncode}: {result.stderr.strip()}")
        return result.stdout

    def wait_until_reachable(self, timeout, vm):
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.run("true", timeout=20)
                return
            except GuestError as e:
                log.debug("SSH to %s not up yet: %s", self.ip, e)
                if vm.poll() is not None:
                    raise GuestError(f"VM exited with {vm.returncode} before SSH came up") from e
                if time.monotonic() > deadline:
                    raise GuestError(f"SSH not reachable after {timeout}s: {e}") from e
            time.sleep(2)

    def install_runner(self, tarball):
        with open(tarball, "rb") as f:
            self.run(f"cat > {RUNNER_TARBALL}", stdin=f, timeout=300)
        # SSH gives the runner a non-login shell, which skips ~/.zprofile, where the image puts
        # Homebrew. Save the login-shell PATH for start_runner; without it, jobs can't find gh.
        self.run(
            f"rm -rf actions-runner && mkdir actions-runner"
            f" && tar -xzf {RUNNER_TARBALL} -C actions-runner && rm {RUNNER_TARBALL}"
            f" && /bin/zsh -lc 'echo $PATH' > actions-runner/.path",
            timeout=300,
        )

    def start_runner(self, jit_config, log):
        # The config goes over stdin so it never appears in the host's process list. Jobs
        # inherit the runner's PATH, and run.sh doesn't read .path (only the runner's service
        # wrapper, runsvc.sh, does), so set it here as runsvc.sh would.
        process = subprocess.Popen(
            self._ssh(
                "read -r config && PATH=$(cat actions-runner/.path)"
                ' && exec actions-runner/run.sh --jitconfig "$config"'
            ),
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=self._env,
        )
        process.stdin.write(jit_config.encode() + b"\n")
        process.stdin.close()
        return process

    def _ssh(self, command):
        return ["ssh", *SSH_OPTIONS, f"{self.username}@{self.ip}", command]
