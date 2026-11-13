"""Minimal GitHub App client: auth, Actions logs, PR data and sticky comments."""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime

import httpx
import jwt

log = logging.getLogger(__name__)

COMMENT_MARKER = "<!-- mender:diagnosis -->"


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Constant-time check of GitHub's X-Hub-Signature-256 header."""
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


@dataclass
class FailedJob:
    id: int
    name: str
    failed_step: str | None
    html_url: str


class GitHubAppAuth:
    """Mints app JWTs and caches installation tokens until shortly before expiry."""

    def __init__(self, app_id: str, private_key: str, api_url: str, http: httpx.Client):
        self.app_id = app_id
        self.private_key = private_key
        self.api_url = api_url
        self.http = http
        self._tokens: dict[int, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def app_jwt(self) -> str:
        now = int(time.time())
        # iat is backdated to tolerate clock drift; GitHub caps exp at 10 minutes.
        payload = {"iat": now - 60, "exp": now + 540, "iss": self.app_id}
        return jwt.encode(payload, self.private_key, algorithm="RS256")

    def installation_token(self, installation_id: int) -> str:
        with self._lock:
            cached = self._tokens.get(installation_id)
            if cached and cached[1] - time.time() > 300:
                return cached[0]
            r = self.http.post(
                f"{self.api_url}/app/installations/{installation_id}/access_tokens",
                headers={"Authorization": f"Bearer {self.app_jwt()}"},
            )
            r.raise_for_status()
            data = r.json()
            expires = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")).timestamp()
            self._tokens[installation_id] = (data["token"], expires)
            return data["token"]


class GitHubClient:
    """Repository-scoped API calls made with an installation token."""

    def __init__(self, auth: GitHubAppAuth, installation_id: int, repo: str):
        self.auth = auth
        self.installation_id = installation_id
        self.repo = repo
        self.http = auth.http
        self.base = f"{auth.api_url}/repos/{repo}"

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.auth.installation_token(self.installation_id)}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _get(self, path: str, accept: str = "application/vnd.github+json", **params):
        r = self.http.get(f"{self.base}{path}", headers=self._headers(accept), params=params,
                          follow_redirects=True)
        r.raise_for_status()
        return r

    # --- Actions -----------------------------------------------------------
    def failed_jobs(self, run_id: int) -> list[FailedJob]:
        jobs = self._get(f"/actions/runs/{run_id}/jobs", filter="latest", per_page=100).json()
        out = []
        for job in jobs.get("jobs", []):
            if job.get("conclusion") not in ("failure", "timed_out"):
                continue
            step = next(
                (s["name"] for s in job.get("steps", []) if s.get("conclusion") == "failure"),
                None,
            )
            out.append(FailedJob(job["id"], job["name"], step, job.get("html_url", "")))
        return out

    def job_log(self, job_id: int) -> str:
        # Responds with a 302 to a short-lived blob URL holding plain text.
        return self._get(f"/actions/jobs/{job_id}/logs").text

    def rerun_failed_jobs(self, run_id: int) -> None:
        r = self.http.post(f"{self.base}/actions/runs/{run_id}/rerun-failed-jobs",
                           headers=self._headers())
        r.raise_for_status()

    # --- Pull requests -----------------------------------------------------
    def prs_for_commit(self, sha: str) -> list[int]:
        """Fallback for fork PRs, whose workflow_run payload has no pull_requests."""
        data = self._get(f"/commits/{sha}/pulls").json()
        return [pr["number"] for pr in data if pr.get("state") == "open"]

    def pull_request(self, number: int) -> dict:
        return self._get(f"/pulls/{number}").json()

    def pull_request_diff(self, number: int) -> str:
        return self._get(f"/pulls/{number}", accept="application/vnd.github.diff").text

    def file_at(self, path: str, ref: str) -> str | None:
        try:
            return self._get(f"/contents/{path}", accept="application/vnd.github.raw+json",
                             ref=ref).text
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    # --- Comments ----------------------------------------------------------
    def upsert_comment(self, pr_number: int, body: str) -> int:
        """Edit Mender's existing comment on the PR, or create one. Returns its id."""
        body = f"{COMMENT_MARKER}\n{body}"
        page = 1
        while True:
            comments = self._get(f"/issues/{pr_number}/comments", per_page=100, page=page).json()
            for c in comments:
                # Only edit comments a bot wrote; anyone can paste the marker into their own.
                is_bot = c.get("user", {}).get("type") == "Bot"
                if is_bot and COMMENT_MARKER in (c.get("body") or ""):
                    r = self.http.patch(f"{self.base}/issues/comments/{c['id']}",
                                        headers=self._headers(), json={"body": body})
                    r.raise_for_status()
                    return c["id"]
            if len(comments) < 100:
                break
            page += 1
        r = self.http.post(f"{self.base}/issues/{pr_number}/comments", headers=self._headers(),
                           json={"body": body})
        r.raise_for_status()
        return r.json()["id"]
