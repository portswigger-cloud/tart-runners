import tempfile
import unittest
from pathlib import Path

from tart_runners import paths
from tart_runners.check import check
from tests.test_slot import config


class NoGitHub:
    def token(self):
        raise AssertionError("a disabled host shouldn't need GitHub")


class CheckTest(unittest.TestCase):
    def test_a_disabled_host_fails_without_waiting(self):
        with tempfile.TemporaryDirectory() as home:
            paths.state_dir(home).mkdir(parents=True)
            paths.disabled_marker(home).write_text("since now: flaky\n")
            sleeps = []
            problems = check(config(), Path(home), wait=600, github=NoGitHub(), sleep=sleeps.append)
        self.assertEqual(problems[0], "disabled since now: flaky; `tart-runners enable` brings it back")
        self.assertEqual(problems[1:], ["slot 1: hasn't started", "slot 2: hasn't started"])
        self.assertEqual(sleeps, [])


if __name__ == "__main__":
    unittest.main()
