"""Minimal GitHub REST client for incident reporting.

An incident becomes a pull request that contains the broken file, so a reviewer
(here: the LLM) can comment on the exact faulty line. The fix is committed to the
same branch and the PR is closed, never merged.
"""
import base64
import os

import httpx


class GitHubError(Exception):
    pass


class GitHub:
    def __init__(self):
        self.token = os.getenv("GITHUB_TOKEN", "").strip()
        self.repo = os.getenv("GITHUB_REPO", "").strip()
        self.base = os.getenv("GITHUB_BASE_BRANCH", "main").strip() or "main"
        self.prefix = os.getenv("GITHUB_SERVICE_PATH", "aidevops/golden").strip("/")
        self.api = os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/")

    @property
    def configured(self):
        return bool(self.token) and "/" in self.repo and not self.repo.startswith("your-username")

    def path(self, svc):
        return f"{self.prefix}/{svc}/main.py"

    def _req(self, method, url, ok404=False, **kw):
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        try:
            r = httpx.request(method, f"{self.api}{url}", headers=headers, timeout=20, **kw)
        except httpx.HTTPError as e:
            raise GitHubError(f"network error: {type(e).__name__}") from None
        if r.status_code == 404 and ok404:
            return None
        if r.status_code >= 400:
            try:
                msg = r.json().get("message", "")
            except ValueError:
                msg = ""
            raise GitHubError(f"{method} {url.split('?')[0]} -> HTTP {r.status_code} {msg[:120]}")
        return r.json() if r.content else {}

    def base_sha(self):
        return self._req("GET", f"/repos/{self.repo}/git/ref/heads/{self.base}")["object"]["sha"]

    def get_file(self, path, ref):
        data = self._req("GET", f"/repos/{self.repo}/contents/{path}?ref={ref}", ok404=True)
        return data

    def create_branch(self, name, sha):
        self._req("POST", f"/repos/{self.repo}/git/refs", json={"ref": f"refs/heads/{name}", "sha": sha})

    def put_file(self, path, content, branch, message, sha):
        body = {"message": message, "branch": branch, "sha": sha,
                "content": base64.b64encode(content.encode("utf-8")).decode()}
        return self._req("PUT", f"/repos/{self.repo}/contents/{path}", json=body)

    def open_pr(self, title, head, body):
        return self._req("POST", f"/repos/{self.repo}/pulls",
                         json={"title": title, "head": head, "base": self.base, "body": body})

    def review(self, number, path, line, comment, summary):
        return self._req("POST", f"/repos/{self.repo}/pulls/{number}/reviews", json={
            "event": "COMMENT", "body": summary,
            "comments": [{"path": path, "line": line, "side": "RIGHT", "body": comment}]})

    def comment(self, number, body):
        return self._req("POST", f"/repos/{self.repo}/issues/{number}/comments", json={"body": body})

    def close_pr(self, number):
        return self._req("PATCH", f"/repos/{self.repo}/pulls/{number}", json={"state": "closed"})
