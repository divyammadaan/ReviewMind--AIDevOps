"""A tiny fake of the GitHub REST endpoints the control plane uses.

    python tests/mock_github.py            # listens on :9999
    GET /_state                            # everything it has received

Lets the incident-PR flow be tested end to end without touching a real repository.
"""
import base64
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

GOLDEN = Path(__file__).resolve().parents[1] / "golden"
STATE = {"branches": {}, "prs": {}, "calls": []}
BASE_SHA = "b" * 40


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("content-length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _route(self, method):
        path = self.path.split("?")[0]
        query = self.path.partition("?")[2]
        if path == "/_state":
            return self._send(200, STATE)
        if not self.headers.get("authorization", "").startswith("Bearer "):
            return self._send(401, {"message": "Bad credentials"})
        body = self._body() if method in ("POST", "PUT", "PATCH") else {}
        STATE["calls"].append(f"{method} {path}")
        m = re.match(r"/repos/[^/]+/[^/]+(/.*)", path)
        if not m:
            return self._send(404, {"message": "Not Found"})
        rest = m.group(1)

        if method == "GET" and rest == "/git/ref/heads/main":
            return self._send(200, {"object": {"sha": BASE_SHA}})
        if method == "POST" and rest == "/git/refs":
            STATE["branches"][body["ref"].removeprefix("refs/heads/")] = {}
            return self._send(201, {"ref": body["ref"]})
        c = re.match(r"/contents/(.+)", rest)
        if c:
            fpath = c.group(1)
            ref = re.search(r"ref=([^&]+)", query)
            ref = ref.group(1) if ref else "main"
            if method == "GET":
                if ref in STATE["branches"] and fpath in STATE["branches"][ref]:
                    text, sha = STATE["branches"][ref][fpath]
                else:
                    svc = fpath.split("/")[-2]
                    src = GOLDEN / svc / "main.py"
                    if not src.exists():
                        return self._send(404, {"message": "Not Found"})
                    text, sha = src.read_text(encoding="utf-8"), "base-" + svc
                return self._send(200, {"sha": sha, "content": base64.b64encode(text.encode()).decode()})
            if method == "PUT":
                text = base64.b64decode(body["content"]).decode()
                sha = f"blob-{len(STATE['calls'])}"
                STATE["branches"].setdefault(body["branch"], {})[fpath] = (text, sha)
                return self._send(200, {"commit": {"sha": sha}})
        if method == "POST" and rest == "/pulls":
            n = len(STATE["prs"]) + 1
            STATE["prs"][str(n)] = {"title": body["title"], "head": body["head"], "body": body["body"],
                                    "state": "open", "reviews": [], "comments": []}
            return self._send(201, {"number": n, "html_url": f"http://localhost:9999/pr/{n}"})
        r = re.match(r"/pulls/(\d+)/reviews", rest)
        if method == "POST" and r:
            STATE["prs"][r.group(1)]["reviews"].append(body)
            return self._send(200, {"id": 1})
        r = re.match(r"/issues/(\d+)/comments", rest)
        if method == "POST" and r:
            STATE["prs"][r.group(1)]["comments"].append(body["body"])
            return self._send(201, {"id": 2})
        r = re.match(r"/pulls/(\d+)$", rest)
        if method == "PATCH" and r:
            STATE["prs"][r.group(1)]["state"] = body["state"]
            return self._send(200, {"state": body["state"]})
        return self._send(404, {"message": "Not Found"})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_PATCH(self):
        self._route("PATCH")


if __name__ == "__main__":
    print("mock GitHub on :9999")
    HTTPServer(("0.0.0.0", 9999), H).serve_forever()
