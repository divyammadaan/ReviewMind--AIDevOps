"""Ops control plane: watches the microservices, injects faults, repairs them with Groq.

Services hot-reload from a shared volume (/runtime), so "breaking" or "fixing" a
service is just rewriting its main.py. This process never touches Docker: CPU and
memory come from the metrics sidecar, which is the only container with the Docker socket.
"""
import difflib
import os
import random
import shutil
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles

load_dotenv()

import fixer  # noqa: E402  (needs env loaded first)
import github as ghmod  # noqa: E402
import signals  # noqa: E402
import telemetry  # noqa: E402
from bugs import BUGS  # noqa: E402
from probes import DEPS, PROBES, Fail  # noqa: E402

RUNTIME, GOLDEN = Path("/runtime"), Path("/golden")
META = {
    "gateway": ("API Gateway", "Edge: serves the storefront, routes /api/* to services", 8080),
    "catalog": ("Catalog", "Product list and prices", 9101),
    "inventory": ("Inventory", "Stock levels and reservations", 9102),
    "cart": ("Cart", "Shopper carts, pricing and the 10% bulk discount", 9103),
    "payments": ("Payments", "Approves or declines charges", 9104),
    "orders": ("Orders", "Checkout: cart + stock + payment", 9105),
}
VERIFY_TIMEOUT = int(os.getenv("VERIFY_TIMEOUT", "14"))  # seconds a deployed patch gets to pass its probe
GRACE = 6  # seconds after a file write during which a failure counts as "restarting"

keys = fixer.Keys()
events: deque = deque(maxlen=80)
lock = threading.Lock()
svcs = {n: {"name": n, "title": t, "about": a, "port": p, "breakable": n != "gateway",
            "status": "starting", "detail": "", "latency_ms": None, "bug": None,
            "last_fix": None, "written_at": 0.0, "fixing": False, "raw_ok": False, "down": False,
            "incident": None, "tokens": 0, "cpu": None, "mem_mb": None, "cpu_hist": deque(maxlen=40),
            "last_good": None, "repair": {"state": "idle", "proposal": None, "rollback": None, "note": ""}}
        for n, (t, a, p) in META.items()}


def log(level, msg, svc=None):
    events.appendleft({"ts": time.strftime("%H:%M:%S"), "t": time.time(), "level": level, "svc": svc, "msg": msg})


def live_file(n):
    return RUNTIME / n / "main.py"


def new_repair():
    return {"state": "idle", "proposal": None, "rollback": None, "note": ""}


def set_repair(n, state, **kw):
    r = svcs[n]["repair"]
    r["state"] = state
    r.update(kw)


def reset_runtime():
    for n in BUGS:
        (RUNTIME / n).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(GOLDEN / n / "main.py", live_file(n))
        svcs[n].update(bug=None, last_fix=None, written_at=time.time(), repair=new_repair(),
                       last_good=(GOLDEN / n / "main.py").read_text(encoding="utf-8"))
    (RUNTIME / "logs").mkdir(parents=True, exist_ok=True)


def log_tail(n, lines=14):
    f = RUNTIME / "logs" / f"{n}.log"
    try:
        return "".join(f.read_text(encoding="utf-8", errors="replace").splitlines(True)[-lines:])
    except OSError:
        return ""


def clear_log(n):
    try:
        (RUNTIME / "logs" / f"{n}.log").write_text("", encoding="utf-8")
    except OSError:
        pass


def write_live(n, code):
    live_file(n).write_text(code, encoding="utf-8")
    svcs[n]["written_at"] = time.time()


# ---------------------------------------------------------------- monitoring

try:
    from opentelemetry import baggage as _baggage, context as _otel_ctx
    from opentelemetry.instrumentation.utils import suppress_instrumentation
except Exception:  # telemetry is optional
    import contextlib
    _baggage = _otel_ctx = None
    suppress_instrumentation = contextlib.nullcontext


def probe_once(n):
    """Return (ok, detail, is_down, latency_ms). Traced as origin=probe (propagated to every service)."""
    token = _otel_ctx.attach(_baggage.set_baggage("origin", "probe")) if _otel_ctx else None
    try:
        return _probe_once(n)
    finally:
        if token:
            _otel_ctx.detach(token)


def _probe_once(n):
    t0 = time.time()
    try:
        PROBES[n]()
        return True, "", False, round((time.time() - t0) * 1000)
    except Fail as e:
        return False, str(e), False, None
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError):
        return False, "service unreachable (container not accepting connections)", True, None
    except Exception as e:  # malformed JSON, KeyError in probe, ...
        return False, f"{type(e).__name__}: {e}", False, None


def monitor():
    pool = ThreadPoolExecutor(max_workers=len(svcs))
    while True:
        results = dict(zip(svcs, pool.map(probe_once, svcs)))
        now = time.time()
        with lock:
            for n, (ok, detail, down, ms) in results.items():
                s = svcs[n]
                s.update(raw_ok=ok, latency_ms=ms, down=down)
                if not ok:
                    s["detail"] = detail
            for n, s in svcs.items():
                ok = s["raw_ok"]
                if s["fixing"]:
                    s["status"] = "fixing"
                elif ok:
                    s["status"], s["detail"] = "healthy", ""
                elif now - s["written_at"] < GRACE:
                    s["status"] = "restarting"
                else:
                    bad = [d for d in DEPS[n] if not svcs[d]["raw_ok"]]
                    if bad:
                        s["status"] = "blocked"
                        s["detail"] = f"upstream unhealthy: {', '.join(bad)}. Not this service's fault yet."
                    else:
                        s["status"] = "down" if s["down"] else "broken"
        time.sleep(3)


def metrics_loop():
    """Poll the metrics sidecar for per-container CPU and memory."""
    while True:
        try:
            with suppress_instrumentation():  # a poll every 1.5 s would drown the trace list
                got = httpx.get("http://metrics:9200/stats", timeout=3).json()["containers"]
        except Exception:  # sidecar down or still starting: show "no data", keep going
            got = {}
        with lock:
            for n, s in svcs.items():
                c = got.get(n)
                s["cpu"], s["mem_mb"] = (c["cpu"], c["mem_mb"]) if c else (None, None)
                if c:
                    s["cpu_hist"].append(c["cpu"])
        time.sleep(1.5)


# ------------------------------------------------------------------- repair

def build_prompt(n, code, symptom, logs, note="", hint=""):
    return (f"Service: {n}\nService map (containers reach each other by hostname): catalog:9101, "
            f"inventory:9102, cart:9103, payments:9104, orders:9105.\n\n"
            f"Failing probe:\n{symptom}\n\nRecent logs:\n{logs.strip() or '(none)'}\n"
            + (f"\nDistributed-trace evidence: {hint}\n" if hint else "")
            + f"{note}\nSource of main.py:\n{code}")


def wait_settled(n):
    """Let an in-flight reload finish, so a diagnosis sees the real failure rather than a restart."""
    end = time.time() + GRACE + 2
    while time.time() < end and time.time() - svcs[n]["written_at"] < GRACE + 1:
        time.sleep(0.5)


def mark_healthy(n):
    """A probe just passed: show it now rather than at the next monitoring cycle."""
    with lock:
        svcs[n].update(raw_ok=True, down=False, detail="", status="healthy")


def wait_healthy(n, seconds=14):
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(1.5)
        ok, detail, _, _ = probe_once(n)
        if ok:
            return True, ""
    return False, detail


def restore_golden(n):
    """Operator-initiated restore of the original code. Discards any pending proposal."""
    s = svcs[n]
    t0 = time.time()
    try:
        before = live_file(n).read_text(encoding="utf-8")
        good = (GOLDEN / n / "main.py").read_text(encoding="utf-8")
        write_live(n, good)
        clear_log(n)
        ok, _ = wait_healthy(n, VERIFY_TIMEOUT)
        if ok:
            s["bug"], s["last_good"] = None, good
            mark_healthy(n)
        s["last_fix"] = {"how": "golden restore", "diff": diff(before, good),
                         "seconds": round(time.time() - t0, 1), "tokens": 0}
        set_repair(n, "resolved" if ok else "failed", proposal=None, rollback=None,
                   note="" if ok else "restore did not recover the service")
        log("ok" if ok else "err", "Restored known-good version." if ok else "Restore did not recover the service.", n)
        if ok and gh_state["enabled"]:
            threading.Thread(target=resolve_incident, daemon=True, args=(
                n, good, "restored the known-good version", 0, s["last_fix"]["seconds"], s["last_fix"]["diff"])).start()
    finally:
        s["fixing"] = False


def make_proposal(n, before, new_code, how, tokens, source, symptom, t0):
    return {"id": f"{int(time.time() * 1000):x}", "code": new_code, "base": before, "diff": diff(before, new_code),
            "how": how, "tokens": tokens, "source": source, "symptom": symptom,
            "created": time.time(), "seconds": round(time.time() - t0, 1)}


def publish_proposal(n, proposal):
    set_repair(n, "pending_approval", proposal=proposal, rollback=None, note="")
    log("info", f"Proposed fix ready ({proposal['how']}). Waiting for human approval; nothing has been applied.", n)
    pr_note(n, f"## Proposed fix, pending approval\n\nSource: **{proposal['how']}**. Nothing has been deployed yet.\n\n"
               f"```diff\n{proposal['diff'].strip()}\n```")


def propose_ai(n, auto_approve=False):
    """Generate a patch with the LLM and hold it for approval. Does NOT touch the running service."""
    s = svcs[n]
    t0 = time.time()
    try:
        set_repair(n, "generating", proposal=None, rollback=None, note="")
        wait_settled(n)
        before = live_file(n).read_text(encoding="utf-8")
        healthy, symptom, _, _ = probe_once(n)
        if healthy:
            log("info", "Service is already healthy, nothing to repair.", n)
            set_repair(n, "idle")
            return
        logs, hint = log_tail(n), telemetry.store.failure_hint(n)
        log("info", f"Diagnosing. Symptom: {symptom[:160]}", n)
        note, total_tokens, proposal = "", 0, None
        for attempt in (1, 2):
            try:
                new_code, meta = fixer.ask(keys, build_prompt(n, before, symptom, logs, note, hint))
            except RuntimeError as e:
                log("err", str(e)[:300], n)
                set_repair(n, "failed", note=str(e)[:200])
                return
            total_tokens += meta["tokens"]
            s["tokens"] += meta["tokens"]
            try:
                compile(new_code, "main.py", "exec")
            except SyntaxError as e:
                log("warn", f"Attempt {attempt}: model returned invalid Python ({e.msg}).", n)
                note = f"\nYour previous reply was not valid Python ({e.msg}). Return the full corrected file."
                continue
            proposal = make_proposal(n, before, new_code, f"AI ({meta['model']}, {meta['key']} key)",
                                     total_tokens, "ai", symptom, t0)
            break
        if proposal is None:
            log("err", "The model did not return valid Python. No proposal was made.", n)
            set_repair(n, "failed", note="model returned invalid Python twice")
            return
        publish_proposal(n, proposal)
        if auto_approve:
            log("warn", "Auto-approve is on (automation mode): applying without human review.", n)
            apply_proposal(n)
    finally:
        s["fixing"] = False


def apply_proposal(n):
    """Deploy the approved patch, verify it, and roll back to the last known-good version if it fails."""
    s = svcs[n]
    r = s["repair"]
    p = r["proposal"]
    t0 = time.time()
    good = s["last_good"] or (GOLDEN / n / "main.py").read_text(encoding="utf-8")
    set_repair(n, "approved")
    log("info", "Patch approved. Deploying with verification (compile + health probe).", n)
    try:
        compile(p["code"], "main.py", "exec")
        write_live(n, p["code"])
        clear_log(n)
        ok, detail = wait_healthy(n, VERIFY_TIMEOUT)
        reason = "" if ok else (detail or f"service did not become healthy within {VERIFY_TIMEOUT}s")
    except SyntaxError as e:
        ok, reason = False, f"patch does not compile: {e.msg}"
    seconds = round(time.time() - t0, 1)
    patch = {k: v for k, v in p.items() if k not in ("code", "base")}

    if ok:
        s["bug"], s["last_good"] = None, p["code"]
        mark_healthy(n)
        s["last_fix"] = {"how": p["how"], "diff": p["diff"], "seconds": round(time.time() - p["created"] + p["seconds"], 1),
                         "tokens": p["tokens"]}
        set_repair(n, "resolved", proposal=patch, rollback=None)
        log("ok", f"Verified and resolved: {p['how']}, {p['tokens']} tokens.", n)
        if gh_state["enabled"]:
            threading.Thread(target=resolve_incident, daemon=True, args=(
                n, p["code"], p["how"], p["tokens"], s["last_fix"]["seconds"], p["diff"])).start()
        return

    log("err", f"Verification failed: {reason[:200]}. Rolling back to the last known-good version.", n)
    write_live(n, good)
    clear_log(n)
    recovered, _ = wait_healthy(n, VERIFY_TIMEOUT)
    if recovered:
        s["bug"], s["last_good"] = None, good
        mark_healthy(n)
    s["last_fix"] = {"how": "patch failed verification: rolled back", "diff": p["diff"],
                     "seconds": round(time.time() - t0, 1), "tokens": p["tokens"]}
    set_repair(n, "rolled_back", proposal=patch,
               rollback={"t": time.time(), "reason": reason[:300], "recovered": recovered})
    log("ok" if recovered else "err",
        "Rolled back; the service is healthy again. The incident is NOT marked resolved."
        if recovered else "Rollback did not recover the service. Use Restore known-good.", n)
    pr_note(n, f"## Patch failed verification and was rolled back\n\n**Reason:** {reason[:300]}\n\n"
               f"The service was restored to its last known-good version "
               f"({'healthy again' if recovered else 'still unhealthy'}). This incident is not resolved.")


def apply_in_background(n):
    try:
        apply_proposal(n)
    finally:
        svcs[n]["fixing"] = False


def pr_note(n, body):
    """Comment on the incident PR (if any). A PR comment is a record only: it is never an approval."""
    inc = svcs[n].get("incident")
    if not inc or not gh_state["enabled"]:
        return

    def work():
        inc["ready"].wait(60)
        if inc.get("number") and inc["state"] == "open":
            try:
                gh.comment(inc["number"], body)
            except ghmod.GitHubError as e:
                log("warn", f"GitHub reporting failed: {e}", n)

    threading.Thread(target=work, daemon=True).start()


def diff(a, b, labels=("broken", "fixed")):
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), *labels, n=1))


# ------------------------------------------------------------ GitHub reporting
# Every incident becomes a pull request holding the broken file. The reviewer (an
# LLM) comments on the faulty line; the fix is committed to the same branch and the
# PR is closed, never merged. Off unless GITHUB_TOKEN and GITHUB_REPO are set.

gh = ghmod.GitHub()
gh_state = {"enabled": gh.configured}
REVIEW_SYSTEM = (
    "You are a senior engineer reviewing a pull request that broke a microservice. Write ONE "
    "review comment of at most three sentences for the changed line: say what is wrong and "
    "exactly what to change it to. Plain text only: no headings, no code fences."
)


def first_changed_line(before, after):
    a, b = before.splitlines(), after.splitlines()
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i + 1
    return min(len(a), len(b)) or 1


def open_incident(n, bug, before, after):
    """Background: wait for detection, open the PR, post the review comment."""
    inc = {"ready": threading.Event(), "number": None, "url": None, "branch": None, "path": gh.path(n),
           "state": "opening"}
    svcs[n]["incident"] = inc
    try:
        end = time.time() + 30
        while time.time() < end and svcs[n]["status"] not in ("broken", "down"):
            time.sleep(1)
        symptom = svcs[n]["detail"] or "service is failing its health probe"
        base_file = gh.get_file(inc["path"], gh.base)
        if base_file is None:
            raise ghmod.GitHubError(f"{inc['path']} not found on {gh.base}: push the repo to GitHub first")
        branch = f"incident/{n}-{time.strftime('%Y%m%d-%H%M%S')}"
        gh.create_branch(branch, gh.base_sha())
        gh.put_file(inc["path"], after, branch, f"Incident: {bug['title']} ({n})", base_file["sha"])
        pr = gh.open_pr(
            f"[Incident] {n}: service failing its health probe", branch,
            f"## Incident in `{n}`\n\nThe service is failing its health probe:\n\n```\n{symptom}\n```\n\n"
            "_Opened automatically by the ReviewMind AIDevOps control plane. This branch records the "
            "incident. It will be closed, not merged, once the service is repaired._")
        inc.update(number=pr["number"], url=pr["html_url"], branch=branch, state="open")
        log("info", f"Opened incident PR #{pr['number']}: {pr['html_url']}", n)

        line = first_changed_line(before, after)
        if keys.live:
            text, meta = fixer.ask(
                keys, f"Service: {n}\nFailing probe: {symptom}\nThe changed line is line {line} of main.py.\n\n"
                      f"Pull request diff:\n{diff(before, after, ('main', 'pull request'))}",
                system=REVIEW_SYSTEM, max_tokens=400, raw=True)
            svcs[n]["tokens"] += meta["tokens"]
        else:
            text = f"This change breaks `{n}` ({symptom}). Revert it."
        gh.review(pr["number"], inc["path"], line, text,
                  f"Automated review: this change makes the `{n}` health probe fail.")
        log("ok", f"Posted review comment on line {line} of PR #{pr['number']}.", n)
    except (ghmod.GitHubError, RuntimeError) as e:
        inc["state"] = "error"
        log("warn", f"GitHub reporting failed: {e}", n)
    finally:
        inc["ready"].set()


def resolve_incident(n, final_code, how, tokens, seconds, patch):
    """Background: commit the fix to the incident branch, comment, close the PR."""
    inc = svcs[n].get("incident")
    if not inc:
        return
    inc["ready"].wait(60)
    if not inc.get("number") or inc["state"] != "open":
        return
    try:
        f = gh.get_file(inc["path"], inc["branch"])
        gh.put_file(inc["path"], final_code, inc["branch"], f"Fix {n}: {how}", f["sha"])
        body = (f"## Resolved\n\n`{n}` is healthy again and passed its probe.\n\n"
                f"- **Repair:** {how}\n- **Time:** {seconds}s\n- **Tokens:** {tokens}\n\n")
        if patch:
            body += f"```diff\n{patch.strip()}\n```\n\n"
        gh.comment(inc["number"], body + "Closing without merging: the live service is already patched.")
        gh.close_pr(inc["number"])
        inc["state"] = "closed"
        log("ok", f"Committed the fix and closed PR #{inc['number']}.", n)
    except ghmod.GitHubError as e:
        log("warn", f"GitHub reporting failed: {e}", n)


# ----------------------------------------------------------------------- API

app = FastAPI(title="AIDevOps control plane")


@app.on_event("startup")
def boot():
    reset_runtime()
    log("info", "Control plane started. All services reset to known-good code.")
    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=metrics_loop, daemon=True).start()


def get_svc(n):
    if n not in svcs or not svcs[n]["breakable"]:
        raise HTTPException(404, "unknown or non-breakable service")
    return svcs[n]


@app.get("/api/state")
def state():
    with lock:
        tele = telemetry.store.stats()
        out = [{k: v for k, v in s.items() if k not in ("written_at", "raw_ok", "down", "fixing", "incident",
                                                         "cpu_hist", "last_good", "repair")}
               | {"deps": DEPS[n], "fixing": s["fixing"], "pr": pr_info(s), "cpu_hist": list(s["cpu_hist"]),
                  "repair": repair_info(s), "tele": tele.get(n)}
               for n, s in svcs.items()]
    github = {"configured": gh.configured, "enabled": gh_state["enabled"], "repo": gh.repo if gh.configured else ""}
    return {"services": out, "llm": keys.public(), "github": github, "events": list(events)}


def repair_info(s):
    r = s["repair"]
    p = r["proposal"]
    return {"state": r["state"], "rollback": r["rollback"], "note": r["note"],
            "proposal": {k: v for k, v in p.items() if k not in ("code", "base")} if p else None}


def pr_info(s):
    inc = s.get("incident")
    if inc and inc.get("number"):
        return {"number": inc["number"], "url": inc["url"], "state": inc["state"]}
    return None


@app.post("/api/services/{n}/break")
def break_service(n: str, bug_id: str | None = None, report: bool = True):
    s = get_svc(n)
    if s["bug"] or s["fixing"] or s["repair"]["state"] == "pending_approval":
        raise HTTPException(409, "service already broken, being repaired, or has a proposal awaiting approval")
    code = live_file(n).read_text(encoding="utf-8")
    if s["raw_ok"]:
        s["last_good"] = code  # the code that is healthy right now is what a failed repair rolls back to
    options = [b for b in BUGS[n] if b["old"] in code and (bug_id is None or b["id"] == bug_id)]
    if not options:
        raise HTTPException(404, "no applicable fault")
    bug = random.choice(options)
    broken = code.replace(bug["old"], bug["new"], 1)
    write_live(n, broken)
    clear_log(n)
    s["bug"], s["last_fix"], s["incident"], s["repair"] = bug["id"], None, None, new_repair()
    log("err", f"Fault injected: {bug['title']}.", n)
    if report and gh_state["enabled"]:
        threading.Thread(target=open_incident, args=(n, bug, code, broken), daemon=True).start()
    return {"injected": bug["id"], "title": bug["title"]}


@app.post("/api/services/{n}/fix")
def fix_service(n: str, mode: str = "ai", auto_approve: bool = False):
    """mode=ai: the LLM proposes a patch and it waits for approval (auto_approve=true skips the wait, for
    automation and tests only). mode=golden: operator-initiated restore of the original code."""
    s = get_svc(n)
    if s["fixing"]:
        raise HTTPException(409, "repair already running")
    if s["repair"]["state"] == "pending_approval" and mode != "golden":
        raise HTTPException(409, "a proposal is already awaiting approval: approve or reject it first")
    if s["status"] == "healthy" and not s["bug"] and mode != "golden":
        raise HTTPException(409, "service is healthy")
    s["fixing"] = True
    if mode == "golden" or not keys.live:
        if mode != "golden":
            log("warn", "No Groq key configured, restoring the known-good version instead.", n)
        threading.Thread(target=restore_golden, args=(n,), daemon=True).start()
    else:
        threading.Thread(target=propose_ai, args=(n, auto_approve), daemon=True).start()
    return {"started": True}


class PatchBody(BaseModel):
    code: str


@app.post("/api/services/{n}/propose")
def propose_manual(n: str, body: PatchBody):
    """Submit a human-written patch. It goes through the same approval, verification and rollback."""
    s = get_svc(n)
    if s["fixing"] or s["repair"]["state"] == "pending_approval":
        raise HTTPException(409, "repair already running or awaiting approval")
    try:
        compile(body.code, "main.py", "exec")
    except SyntaxError as e:
        raise HTTPException(400, f"patch does not compile: {e.msg} (line {e.lineno})")
    before = live_file(n).read_text(encoding="utf-8")
    publish_proposal(n, make_proposal(n, before, body.code, "Manual patch (operator)", 0, "manual",
                                      s["detail"] or "", time.time()))
    return {"pending": True}


@app.post("/api/services/{n}/approve")
def approve(n: str):
    s = get_svc(n)
    r = s["repair"]
    if r["state"] != "pending_approval" or not r["proposal"]:
        raise HTTPException(409, "no proposal is awaiting approval")
    if s["fixing"]:
        raise HTTPException(409, "repair already running")
    if live_file(n).read_text(encoding="utf-8") != r["proposal"]["base"]:
        set_repair(n, "idle", proposal=None)
        log("warn", "Proposal discarded: the service changed after it was generated.", n)
        raise HTTPException(409, "the service changed since this proposal was generated; request a new one")
    s["fixing"] = True
    threading.Thread(target=apply_in_background, args=(n,), daemon=True).start()
    return {"approved": True}


@app.post("/api/services/{n}/reject")
def reject(n: str, reason: str = ""):
    s = get_svc(n)
    r = s["repair"]
    if r["state"] != "pending_approval" or not r["proposal"]:
        raise HTTPException(409, "no proposal is awaiting approval")
    p = {k: v for k, v in r["proposal"].items() if k not in ("code", "base")}
    set_repair(n, "rejected", proposal=p, note=reason[:200])
    log("warn", "Proposal rejected by the operator. The running service was not modified.", n)
    pr_note(n, "## Proposed fix rejected\n\nA human rejected the proposal; nothing was deployed.")
    return {"rejected": True}


@app.post("/api/chaos")
def chaos(count: int = 2):
    pool = [n for n in BUGS if not svcs[n]["bug"] and not svcs[n]["fixing"]]
    for n in random.sample(pool, min(count, len(pool))):
        break_service(n)
    return {"ok": True}


@app.post("/api/reset")
def reset():
    for n, s in svcs.items():
        if s.get("incident") and gh_state["enabled"]:
            threading.Thread(target=resolve_incident, daemon=True, args=(
                n, (GOLDEN / n / "main.py").read_text(encoding="utf-8"), "reset to the known-good version",
                0, 0, "")).start()
    reset_runtime()
    log("info", "All services reset to known-good code.")
    return {"ok": True}


# ----------------------------------------------------------------- telemetry
# OTLP/HTTP receiver: every service (and this control plane) exports its spans here.

@app.post("/v1/traces")
async def otlp_traces(request: Request):
    try:
        telemetry.store.ingest(await request.body(), request.headers.get("content-encoding", ""))
    except Exception:
        return Response(status_code=400)
    return Response(b"", media_type="application/x-protobuf")


@app.get("/api/traces")
def traces(kind: str = "errors", limit: int = 20):
    return {"traces": telemetry.store.list(kind, min(limit, 100))}


@app.get("/api/traces/{trace_id}")
def trace_detail(trace_id: str):
    d = telemetry.store.detail(trace_id)
    if d is None:
        raise HTTPException(404, "trace not found (it may have been evicted)")
    return d


@app.get("/api/telemetry")
def telemetry_stats():
    return {"spans_received": telemetry.store.received, "last_received": telemetry.store.last_received,
            "metrics_received": signals.metric_store.received, "logs_received": signals.log_store.received,
            "services": telemetry.store.stats()}


@app.post("/v1/metrics")
async def otlp_metrics(request: Request):
    try:
        signals.metric_store.ingest(await request.body(), request.headers.get("content-encoding", ""))
    except Exception:
        return Response(status_code=400)
    return Response(b"", media_type="application/x-protobuf")


@app.post("/v1/logs")
async def otlp_logs(request: Request):
    try:
        signals.log_store.ingest(await request.body(), request.headers.get("content-encoding", ""))
    except Exception:
        return Response(status_code=400)
    return Response(b"", media_type="application/x-protobuf")


@app.get("/api/metrics")
def metrics_view(window: int = 60):
    return {"window_s": window, "services": signals.metric_store.stats(min(max(window, 10), 600)),
            "received": signals.metric_store.received}


@app.get("/api/logs")
def logs_view(service: str | None = None, level: str = "warn", trace_id: str | None = None,
              q: str | None = None, limit: int = 100):
    return {"logs": signals.log_store.query(service, level, trace_id, q, min(limit, 300)),
            "received": signals.log_store.received}


@app.get("/api/traces/{trace_id}/logs")
def trace_logs(trace_id: str):
    """Logs stamped with this trace's ID, plus (marked as such) untagged logs from the same services
    around the same time, because some log lines are written outside the request's span."""
    d = telemetry.store.detail(trace_id)
    by_trace = signals.log_store.query(trace_id=trace_id, level="all", limit=200)
    by_time = []
    if d:
        end = d["start"] + d["duration_ms"] / 1000
        by_time = signals.log_store.around(set(d["services"]), d["start"], end)
    return {"by_trace": sorted(by_trace, key=lambda r: r["t"]), "by_time": by_time}


@app.post("/api/github")
def github_toggle(enabled: bool):
    if enabled and not gh.configured:
        raise HTTPException(409, "set GITHUB_TOKEN and GITHUB_REPO in .env first")
    gh_state["enabled"] = enabled
    log("info", f"GitHub reporting {'on' if enabled else 'off'}.")
    return {"enabled": enabled}


@app.get("/api/health")
def health():
    return {"ok": True}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
