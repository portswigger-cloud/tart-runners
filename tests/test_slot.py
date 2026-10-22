import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tart_runners import paths, slot as slot_module
from tart_runners.config import GitHub as GitHubConfig, VM, Config
from tart_runners.github import GitHubError
from tart_runners.guest import GuestError
from tart_runners.slot import Slot, read_status, runner_name


class Process:
    def __init__(self, returncode=0):
        self.returncode = None
        self._exit = returncode

    def poll(self):
        self.returncode = self._exit
        return self.returncode

    def wait(self, timeout=None):
        return self.poll()

    def terminate(self):
        pass

    kill = terminate


class Job(Process):
    """A runner that stays up until terminated, or until it has been polled `polls` times."""

    def __init__(self, polls=None):
        super().__init__()
        self.polls = polls
        self.terminated = False

    def poll(self):
        if self.terminated:
            self.returncode = -15
        elif self.polls is not None:
            self.polls -= 1
            if self.polls < 0:
                self.returncode = 0
        return self.returncode

    def terminate(self):
        self.terminated = True


class FakeTart:
    def __init__(self):
        self.vms = set()
        self.calls = []
        self.vm = None

    def exists(self, name):
        return name in self.vms

    def clone(self, image, name, insecure):
        self.calls.append(("clone", image, name, insecure))
        self.vms.add(name)

    def configure(self, name, cpu, memory_gb):
        pass

    def mac(self, name):
        return "aa:bb:cc:dd:ee:ff"

    def run(self, name, log):
        self.calls.append(("run", name))
        return self.vm or Process()

    def ip(self, name, wait):
        return "192.168.64.2"

    def stop(self, name):
        self.calls.append(("stop", name))

    def delete(self, name):
        self.calls.append(("delete", name))
        self.vms.discard(name)


class FakeGitHub:
    def __init__(self):
        self.deleted = []
        self.registered = []
        self.running = None

    def token(self):
        return "ghs_x"

    def runner_group_id(self, name):
        return 1

    def jit_config(self, name, labels, group_id):
        self.registered.append((name, labels, group_id))
        return 99, "jit"

    def delete_runner(self, runner_id):
        if self.running is not None and self.running.returncode is None:
            raise GitHubError(422, "runner is still running a job")
        self.deleted.append(runner_id)


class FakeGuest:
    runner_exit = 0
    fail_on = None
    job = None

    def __init__(self, ip, username, password):
        pass

    def wait_until_reachable(self, timeout, vm):
        if self.fail_on == "ssh":
            raise GuestError("no ssh")

    def install_runner(self, tarball):
        self.tarball = tarball

    def start_runner(self, jit_config, log):
        return self.job or Process(self.runner_exit)


def config(slots=2):
    return Config(
        host="Mini",
        github=GitHubConfig("org", 1, Path("/key.pem"), "Default", ("self-hosted", "x")),
        vm=VM("registry/image:tag", True, "admin", "admin", None, None),
        runner_version="latest",
        slots=slots,
        max_vm_hours=24,
    )


class SlotTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.home = Path(self.dir.name)
        self.config_path = self.home / "config.toml"
        self.config_path.write_text("x")
        self.tart = FakeTart()
        self.github = FakeGitHub()
        patches = [
            mock.patch.object(slot_module, "Guest", FakeGuest),
            mock.patch.object(slot_module.RunnerReleases, "tarball", return_value=self.home / "runner.tar.gz"),
            mock.patch.object(slot_module.time, "sleep"),
            mock.patch.object(slot_module, "DRAIN_RETRY", 0),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        FakeGuest.runner_exit = 0
        FakeGuest.fail_on = None
        FakeGuest.job = None

    def tearDown(self):
        self.dir.cleanup()

    def slot(self):
        s = Slot(config(), self.config_path, 1, self.home, tart=self.tart, github=self.github)
        s.state.mkdir(parents=True)
        s.logs.mkdir(parents=True)
        return s

    def test_runs_one_job_and_cleans_up(self):
        self.slot().run_once()
        self.assertIn(("clone", "registry/image:tag", "tart-runners-1", True), self.tart.calls)
        self.assertEqual(self.tart.vms, set())
        name, labels, group = self.github.registered[0]
        self.assertTrue(name.startswith("mini-1-"))
        self.assertEqual((labels, group), (("self-hosted", "x"), 1))
        self.assertEqual(self.github.deleted, [99])
        self.assertEqual(read_status(self.home, 1)["state"], "ready")

    def test_removes_the_vm_when_ssh_never_comes_up(self):
        FakeGuest.fail_on = "ssh"
        with self.assertRaises(GuestError):
            self.slot().run_once()
        self.assertEqual(self.tart.vms, set())
        self.assertEqual(self.github.registered, [])

    def test_a_failed_runner_is_an_error(self):
        FakeGuest.runner_exit = 1
        with self.assertRaisesRegex(GuestError, "runner exited with 1"):
            self.slot().run_once()
        self.assertEqual(self.github.deleted, [99])
        self.assertEqual(self.tart.vms, set())

    def test_restarts_when_the_config_changes(self):
        s = self.slot()
        self.assertFalse(s._outdated())
        self.config_path.write_text("y")
        self.assertTrue(s._outdated())

    def disable(self):
        paths.disabled_marker(self.home).write_text("since now: flaky\n")

    def test_disabling_stops_an_idle_runner(self):
        s = self.slot()
        self.tart.vm = Job()
        FakeGuest.job = Job()
        self.disable()
        s.run_once()
        self.assertTrue(FakeGuest.job.terminated)
        self.assertEqual(self.github.deleted, [99, 99])
        self.assertEqual(self.tart.vms, set())

    def test_disabling_lets_a_busy_runner_finish_its_job(self):
        s = self.slot()
        self.tart.vm = Job()
        FakeGuest.job = self.github.running = Job(polls=3)
        self.disable()
        s.run_once()
        self.assertFalse(FakeGuest.job.terminated)
        self.assertEqual(FakeGuest.job.returncode, 0)
        self.assertEqual(self.tart.vms, set())

    def test_a_disabled_slot_boots_nothing(self):
        s = self.slot()
        self.disable()
        with mock.patch.object(Slot, "_outdated", side_effect=[False, False, True]):
            s.run_forever()
        self.assertNotIn("clone", [call[0] for call in self.tart.calls])
        status = read_status(self.home, 1)
        self.assertEqual((status["state"], status["detail"]), ("disabled", "since now: flaky"))

    def test_runner_names_are_unique_per_boot(self):
        self.assertNotEqual(runner_name("Mini", 1), runner_name("Mini", 1))
        self.assertRegex(runner_name("Mini", 2), r"^mini-2-[0-9a-f]{6}$")


if __name__ == "__main__":
    unittest.main()
