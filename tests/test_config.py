import tomllib
import unittest
from pathlib import Path

from tart_runners.config import ConfigError, parse

EXAMPLE = Path(__file__).parent.parent / "examples/config.toml"
HOME = Path("/Users/runner")


def load(text=None, host="mini", hardware=(8, 24)):
    data = tomllib.loads(text) if text is not None else tomllib.loads(EXAMPLE.read_text())
    return parse(data, home=HOME, host=host, hardware=hardware)


MINIMAL = """
[github]
organization = "org"
app_id = 1
private_key = "~/key.pem"
labels = ["a"]
[vm]
image = "registry/image:tag"
[machines.m2]
cores = 8
memory_gb = 24
slots = 2
vm = { cpu = 4, memory_gb = 10 }
"""


class ConfigTest(unittest.TestCase):
    def test_example_parses(self):
        config = load()
        self.assertEqual(config.github.private_key, HOME / "github.pem")
        self.assertEqual(config.github.labels, ("self-hosted", "macOS", "ARM64", "macos-arm64"))
        self.assertEqual((config.slots, config.vm.cpu, config.vm.memory_gb), (2, 4, 10))

    def test_defaults(self):
        config = load(MINIMAL)
        self.assertEqual(config.github.runner_group, "Default")
        self.assertEqual((config.vm.username, config.vm.password), ("admin", "admin"))
        self.assertFalse(config.vm.insecure)
        self.assertEqual(config.runner_version, "latest")
        self.assertEqual(config.max_vm_hours, 24)

    def test_machine_is_chosen_by_cores_and_memory(self):
        config = load(hardware=(12, 48))
        self.assertEqual((config.slots, config.vm.cpu, config.vm.memory_gb), (2, 6, 22))
        self.assertEqual(config.github.labels, ("self-hosted", "macOS", "ARM64", "macos-arm64", "large"))
        self.assertEqual(load(hardware=(8, 16)).vm.memory_gb, 7)

    def test_rejects_an_unknown_machine(self):
        with self.assertRaisesRegex(ConfigError, "no \\[machines\\] entry for a Mac with 14 cores and 48GB"):
            load(hardware=(14, 48))

    def test_rejects_two_entries_for_one_machine(self):
        with self.assertRaisesRegex(ConfigError, "more than one"):
            load(MINIMAL + "[machines.again]\ncores = 8\nmemory_gb = 24\nslots = 1\nvm = { cpu = 8, memory_gb = 20 }\n")

    def test_rejects_more_slots_than_the_licence_allows(self):
        with self.assertRaisesRegex(ConfigError, "slots"):
            load(MINIMAL.replace("slots = 2", "slots = 3"))

    def test_rejects_vms_that_use_all_the_memory(self):
        with self.assertRaisesRegex(ConfigError, "leave the host no memory"):
            load(MINIMAL.replace("memory_gb = 10", "memory_gb = 12"))

    def test_rejects_unknown_keys(self):
        with self.assertRaisesRegex(ConfigError, "unknown keys: imge"):
            load(MINIMAL + 'imge = "typo"\n')

    def test_rejects_wrong_types(self):
        with self.assertRaisesRegex(ConfigError, "app_id"):
            load(MINIMAL.replace("app_id = 1", 'app_id = "1"'))
        with self.assertRaisesRegex(ConfigError, "app_id"):
            load(MINIMAL.replace("app_id = 1", "app_id = true"))

    def test_runner_version(self):
        self.assertEqual(load(MINIMAL + '[runner]\nversion = "2.337.0"\n').runner_version, "2.337.0")
        with self.assertRaisesRegex(ConfigError, "runner.version"):
            load(MINIMAL + '[runner]\nversion = "v2.337.0"\n')

    def test_requires_labels(self):
        with self.assertRaisesRegex(ConfigError, "labels"):
            load(MINIMAL.replace('labels = ["a"]', "labels = []"))


if __name__ == "__main__":
    unittest.main()
