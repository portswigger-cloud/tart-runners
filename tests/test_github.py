import base64
import io
import json
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path

from tart_runners.github import CredentialError, GitHub, GitHubError, app_jwt


def b64decode(part):
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class FakeAPI:
    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, request, timeout):
        key = (request.get_method(), request.full_url.removeprefix("https://api.github.com"))
        self.requests.append((key, request))
        status, body = self.routes[key]
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "error", {}, io.BytesIO(json.dumps(body).encode()))
        return Response(json.dumps(body).encode() if body is not None else b"")


class GitHubTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.key = Path(cls.dir.name) / "key.pem"
        cls.public = Path(cls.dir.name) / "key.pub"
        subprocess.run(["openssl", "genrsa", "-out", cls.key, "2048"], check=True, capture_output=True)
        subprocess.run(["openssl", "rsa", "-in", cls.key, "-pubout", "-out", cls.public], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def api(self, extra=None):
        return FakeAPI({
            ("GET", "/orgs/org/installation"): (200, {"id": 7}),
            ("POST", "/app/installations/7/access_tokens"): (201, {"token": "ghs_x", "expires_at": "2099-01-01T00:00:00Z"}),
            **(extra or {}),
        })

    def test_jwt_is_signed_with_the_app_key(self):
        token = app_jwt(42, self.key, now=1000)
        header, payload, signature = token.split(".")
        self.assertEqual(json.loads(b64decode(header)), {"alg": "RS256", "typ": "JWT"})
        self.assertEqual(json.loads(b64decode(payload)), {"iat": 940, "exp": 1540, "iss": "42"})
        with tempfile.NamedTemporaryFile() as sig:
            sig.write(b64decode(signature))
            sig.flush()
            verified = subprocess.run(
                ["openssl", "dgst", "-sha256", "-verify", self.public, "-signature", sig.name],
                input=f"{header}.{payload}".encode(),
                capture_output=True,
            )
        self.assertEqual(verified.returncode, 0, verified.stderr)

    def test_missing_key(self):
        with self.assertRaisesRegex(CredentialError, "missing"):
            GitHub("org", 1, Path(self.dir.name) / "absent.pem", urlopen=self.api()).token()

    def test_token_is_cached(self):
        api = self.api()
        github = GitHub("org", 1, self.key, urlopen=api)
        self.assertEqual(github.token(), "ghs_x")
        self.assertEqual(github.token(), "ghs_x")
        self.assertEqual(len(api.requests), 2)

    def test_jit_config(self):
        api = self.api({
            ("POST", "/orgs/org/actions/runners/generate-jitconfig"): (201, {"runner": {"id": 5}, "encoded_jit_config": "abc"}),
        })
        github = GitHub("org", 1, self.key, urlopen=api)
        self.assertEqual(github.jit_config("host-1-abc", ("self-hosted", "x"), 1), (5, "abc"))
        body = json.loads(api.requests[-1][1].data)
        self.assertEqual(body, {"name": "host-1-abc", "runner_group_id": 1, "labels": ["self-hosted", "x"], "work_folder": "_work"})
        self.assertEqual(api.requests[-1][1].get_header("Authorization"), "Bearer ghs_x")

    def test_delete_ignores_runners_that_are_already_gone(self):
        api = self.api({("DELETE", "/orgs/org/actions/runners/5"): (404, {"message": "Not Found"})})
        GitHub("org", 1, self.key, urlopen=api).delete_runner(5)

    def test_runner_group_lookup(self):
        api = self.api({
            ("GET", "/orgs/org/actions/runner-groups?per_page=100&page=1"): (200, {"runner_groups": [{"id": 1, "name": "Default"}]}),
        })
        github = GitHub("org", 1, self.key, urlopen=api)
        self.assertEqual(github.runner_group_id("Default"), 1)
        with self.assertRaisesRegex(GitHubError, "Other"):
            github.runner_group_id("Other")


if __name__ == "__main__":
    unittest.main()
