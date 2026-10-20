import tomllib
import unittest
from pathlib import Path

from tart_runners.config import ConfigError, TWO_SLOT_MEMORY, parse

EXAMPLE = Path(__file__).parent.parent / "examples/config.toml"
HOME = Path("/Users/runner")


def load(text=None, host="mini", memory=TWO_SLOT_MEMORY):
    data = tomllib.loads(text) if text is not None else tomllib.loads(EXAMPLE.read_text())
    return parse(data, home=HOME, host=host, memory=memory)


MINIMAL = """
[github]
organization = "org"
app_id = 1
private_key = "~/key.pem"
labels = ["a"]
[vm]
image = "registry/image:tag"
"""


class ConfigTest(unittest.TestCase):
    def test_example_parses(self):
        config = load()
        self.assertEqual(config.github.private_key, HOME / "github.pem")
        self.assertEqual(config.github.labels, ("self-hosted", "macOS", "ARM64", "macos-arm64"))
        self.assertEqual(config.slots, 2)

    def test_defaults(self):
        config = load(MINIMAL)
        self.assertEqual(config.github.runner_group, "Default")
        self.assertEqual((config.vm.username, config.vm.password), ("admin", "admin"))
        self.assertFalse(config.vm.insecure)
        self.assertEqual(config.runner_version, "latest")
        self.assertEqual(config.max_vm_hours, 24)

    def test_auto_slots_follow_memory(self):
        self.assertEqual(load(MINIMAL, memory=16 * 1024**3).slots, 1)
        self.assertEqual(load(MINIMAL, memory=TWO_SLOT_MEMORY).slots, 2)

    def test_host_override(self):
        text = MINIMAL + '[hosts.Big]\nlabels = ["b"]\nslots = 1\n'
        config = load(text, host="big")
        self.assertEqual(config.github.labels, ("self-hosted", "macOS", "ARM64", "b"))
        self.assertEqual(config.slots, 1)
        self.assertEqual(load(text, host="other").slots, 2)

    def test_rejects_more_slots_than_the_licence_allows(self):
        with self.assertRaisesRegex(ConfigError, "slots"):
            load(MINIMAL + "[host]\nslots = 3\n")

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
