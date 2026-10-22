import fcntl
import hashlib
import json
import logging
import os
import re
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

PLATFORM = "osx-arm64"
KEEP = 3
REFRESH = 86400


class ReleaseError(Exception):
    pass


def asset_name(version):
    return f"actions-runner-{PLATFORM}-{version}.tar.gz"


def parse_release(release):
    version = release["tag_name"].removeprefix("v")
    name = asset_name(version)
    for asset in release.get("assets", []):
        if asset["name"] == name:
            digest = asset.get("digest") or ""
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise ReleaseError(f"{name} has no SHA-256 digest in release {version}")
            return version, asset["browser_download_url"], digest.removeprefix("sha256:")
    raise ReleaseError(f"release {version} has no {name}")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


class RunnerReleases:
    """Downloads the Actions runner on the host and verifies it, so guests never fetch it themselves."""

    def __init__(self, cache, version, github, urlopen=urllib.request.urlopen):
        self.cache = Path(cache)
        self.version = version
        self.github = github
        self._urlopen = urlopen

    def tarball(self):
        self.cache.mkdir(parents=True, exist_ok=True)
        with self._lock():
            if self.version != "latest":
                path = self.cache / asset_name(self.version)
                if self._verified(path):
                    return path
            try:
                return self._fetch(*parse_release(self._release()))
            except Exception as e:
                fallback = self._newest_verified()
                if fallback is None:
                    raise
                log.warning("using cached %s, can't fetch runner %s: %s", fallback.name, self.version, e)
                return fallback

    def _release(self):
        index = self.cache / f"release-{self.version}.json"
        try:
            if self.version != "latest" or time.time() - index.stat().st_mtime < REFRESH:
                return json.loads(index.read_text())
        except (OSError, ValueError):
            pass
        release = self.github.runner_release(self.version)
        _write_atomic(index, json.dumps(release).encode())
        return release

    def _fetch(self, version, url, digest):
        path = self.cache / asset_name(version)
        if self._verified(path) and _read(_sidecar(path)) == digest:
            return path
        log.info("downloading runner %s", version)
        partial = path.with_name(f".{path.name}.partial")
        hasher = hashlib.sha256()
        with self._urlopen(url, timeout=300) as response, open(partial, "wb") as f:
            while chunk := response.read(1 << 20):
                hasher.update(chunk)
                f.write(chunk)
        if hasher.hexdigest() != digest:
            partial.unlink()
            raise ReleaseError(f"{path.name} SHA-256 is {hasher.hexdigest()}, GitHub published {digest}")
        os.replace(partial, path)
        _write_atomic(_sidecar(path), digest.encode())
        self._prune()
        return path

    def _verified(self, path):
        expected = _read(_sidecar(path))
        return path.is_file() and expected is not None and sha256(path) == expected

    def _tarballs(self):
        pattern = re.compile(rf"actions-runner-{PLATFORM}-(\d+(?:\.\d+)+)\.tar\.gz")
        found = []
        for path in self.cache.iterdir():
            if match := pattern.fullmatch(path.name):
                found.append((tuple(int(p) for p in match[1].split(".")), path))
        return [path for _, path in sorted(found, reverse=True)]

    def _newest_verified(self):
        return next((p for p in self._tarballs() if self._verified(p)), None)

    def _prune(self):
        for path in self._tarballs()[KEEP:]:
            path.unlink(missing_ok=True)
            _sidecar(path).unlink(missing_ok=True)

    @contextmanager
    def _lock(self):
        with open(self.cache / ".lock", "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield


def _sidecar(path):
    return path.with_name(path.name + ".sha256")


def _read(path):
    try:
        return path.read_text().strip() or None
    except OSError:
        return None


def _write_atomic(path, data):
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
