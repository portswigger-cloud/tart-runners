import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from tart_runners.releases import ReleaseError, RunnerReleases, asset_name, parse_release

TARBALL = b"runner bytes"
DIGEST = hashlib.sha256(TARBALL).hexdigest()


def release(version, digest=DIGEST):
    name = asset_name(version)
    return {
        "tag_name": f"v{version}",
        "assets": [
            {"name": "actions-runner-linux-x64-1.tar.gz", "digest": "sha256:" + "0" * 64, "browser_download_url": "x"},
            {"name": name, "digest": f"sha256:{digest}", "browser_download_url": f"https://example/{name}"},
        ],
    }


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class FakeGitHub:
    def __init__(self, releases):
        self.releases = releases
        self.calls = 0

    def runner_release(self, version):
        self.calls += 1
        if isinstance(self.releases, Exception):
            raise self.releases
        return self.releases[version]


class ReleasesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cache = Path(self.dir.name)
        self.downloads = []

    def tearDown(self):
        self.dir.cleanup()

    def urlopen(self, url, timeout):
        self.downloads.append(url)
        return Response(TARBALL)

    def releases(self, version, github):
        return RunnerReleases(self.cache, version, github, urlopen=self.urlopen)

    def test_parse_release_picks_the_macos_arm64_asset(self):
        version, url, digest = parse_release(release("2.337.0"))
        self.assertEqual((version, digest), ("2.337.0", DIGEST))
        self.assertTrue(url.endswith("actions-runner-osx-arm64-2.337.0.tar.gz"))

    def test_parse_release_requires_a_digest(self):
        data = release("2.337.0")
        del data["assets"][1]["digest"]
        with self.assertRaisesRegex(ReleaseError, "digest"):
            parse_release(data)

    def test_downloads_once_and_verifies(self):
        github = FakeGitHub({"latest": release("2.337.0")})
        releases = self.releases("latest", github)
        path = releases.tarball()
        self.assertEqual(path.read_bytes(), TARBALL)
        self.assertEqual(releases.tarball(), path)
        self.assertEqual((len(self.downloads), github.calls), (1, 1))

    def test_rejects_a_tampered_download(self):
        github = FakeGitHub({"latest": release("2.337.0", digest="f" * 64)})
        with self.assertRaisesRegex(ReleaseError, "SHA-256"):
            self.releases("latest", github).tarball()
        self.assertFalse((self.cache / asset_name("2.337.0")).exists())

    def test_pinned_version_needs_no_api_call_once_cached(self):
        self.releases("2.337.0", FakeGitHub({"2.337.0": release("2.337.0")})).tarball()
        github = FakeGitHub(RuntimeError("offline"))
        self.releases("2.337.0", github).tarball()
        self.assertEqual(github.calls, 0)

    def test_falls_back_to_the_newest_verified_tarball(self):
        self.releases("latest", FakeGitHub({"latest": release("2.337.0")})).tarball()
        (self.cache / "release-latest.json").unlink()
        path = self.releases("latest", FakeGitHub(RuntimeError("offline"))).tarball()
        self.assertEqual(path.name, asset_name("2.337.0"))

    def test_keeps_three_versions(self):
        for version in ("2.1.0", "2.10.0", "2.2.0", "2.3.0"):
            self.releases(version, FakeGitHub({version: release(version)})).tarball()
        kept = sorted(p.name for p in self.cache.glob("actions-runner-*.tar.gz"))
        self.assertEqual(kept, [asset_name(v) for v in ("2.10.0", "2.2.0", "2.3.0")])


if __name__ == "__main__":
    unittest.main()
