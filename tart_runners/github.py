import base64
import json
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime

API = "https://api.github.com"
# Refresh an installation token this long before GitHub expires it.
TOKEN_MARGIN = 300


class GitHubError(Exception):
    def __init__(self, status, message):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


class CredentialError(Exception):
    pass


def app_jwt(app_id, private_key, now=None):
    now = int(now if now is not None else time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    # iat is backdated to allow for clock drift between us and GitHub.
    claims = {"iat": now - 60, "exp": now + 540, "iss": str(app_id)}
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    try:
        result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(private_key)],
            input=signing_input,
            capture_output=True,
        )
    except OSError as e:
        raise CredentialError(f"can't run openssl: {e}") from e
    if result.returncode != 0:
        raise CredentialError(f"can't sign with {private_key}: {result.stderr.decode().strip()}")
    return f"{header}.{payload}.{_b64url(result.stdout)}"


class GitHub:
    def __init__(self, organization, app_id, private_key, urlopen=urllib.request.urlopen):
        self.organization = organization
        self.app_id = app_id
        self.private_key = private_key
        self._urlopen = urlopen
        self._token = None
        self._token_expires = 0.0

    def token(self):
        if self._token and time.time() < self._token_expires - TOKEN_MARGIN:
            return self._token
        if not self.private_key.is_file():
            raise CredentialError(f"GitHub App private key {self.private_key} is missing")
        jwt = app_jwt(self.app_id, self.private_key)
        installation = self._request("GET", f"/orgs/{self.organization}/installation", token=jwt)
        response = self._request("POST", f"/app/installations/{installation['id']}/access_tokens", token=jwt)
        self._token = response["token"]
        self._token_expires = datetime.fromisoformat(response["expires_at"]).timestamp()
        return self._token

    def runner_group_id(self, name):
        for group in self._paginate(f"/orgs/{self.organization}/actions/runner-groups", "runner_groups"):
            if group["name"] == name:
                return group["id"]
        raise GitHubError(404, f"runner group {name!r} not found in {self.organization}")

    def jit_config(self, name, labels, runner_group_id):
        response = self._request(
            "POST",
            f"/orgs/{self.organization}/actions/runners/generate-jitconfig",
            body={"name": name, "runner_group_id": runner_group_id, "labels": list(labels), "work_folder": "_work"},
        )
        return response["runner"]["id"], response["encoded_jit_config"]

    def delete_runner(self, runner_id):
        try:
            self._request("DELETE", f"/orgs/{self.organization}/actions/runners/{runner_id}")
        except GitHubError as e:
            if e.status != 404:
                raise

    def runners(self):
        return list(self._paginate(f"/orgs/{self.organization}/actions/runners", "runners"))

    def runner_release(self, version):
        tag = "latest" if version == "latest" else f"tags/v{version}"
        return self._request("GET", f"/repos/actions/runner/releases/{tag}")

    def _paginate(self, path, key):
        page = 1
        while True:
            response = self._request("GET", f"{path}?per_page=100&page={page}")
            yield from response[key]
            if len(response[key]) < 100:
                return
            page += 1

    def _request(self, method, path, body=None, token=None):
        request = urllib.request.Request(
            API + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token or self.token()}",
                "User-Agent": "tart-runners",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with self._urlopen(request, timeout=30) as response:
                content = response.read()
        except urllib.error.HTTPError as e:
            raise GitHubError(e.code, e.read().decode(errors="replace")[:500]) from None
        except urllib.error.URLError as e:
            raise GitHubError(0, str(e.reason)) from None
        return json.loads(content) if content else None


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
