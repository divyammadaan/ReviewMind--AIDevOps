"""Ops control plane: watches the microservices, injects faults, repairs them with Groq.

Services hot-reload from a shared volume (/runtime), so "breaking" or "fixing" a
service is just rewriting its main.py. No Docker socket needed.
"""
import difflib
import random
import shutil
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

load_dotenv()

import fixer  # noqa: E402  (needs env loaded first)
import github as ghmod  # noqa: E402
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
GRACE = 6  # seconds after a file write during which a failure counts as "restarting"

keys = fixer.Keys()
events: deque = deque(maxlen=80)
lock = threading.Lock()
svcs = {n: {"name": n, "title": t, "about": a, "port": p, "breakable": n != "gateway",
            "status": "starting", "detail": "", "latency_ms": None, "bug": None,
            "last_fix": None, "written_at": 0.0, "fixing": False, "raw_ok": False, "down": False,
            "incident": None}
        for n, (t, a, p) in META.items()}


def log(level, msg, svc=None):
    events.appendleft({"ts": time.strftime("%H:%M:%S"), "level": level, "svc": svc, "msg": msg})


def live_file(n):
    return RUNTIME / n / "main.py"


def reset_runtime():
    for n in BUGS:
        (RUNTIME / n).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(GOLDEN / n / "main.py", live_file(n))
        svcs[n].update(bug=None, last_fix=None, written_at=time.time())
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

def probe_once(n):
    """Return (ok, detail, is_down, latency_ms)."""
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


# ------------------------------------------------------------------- repair

def build_prompt(n, code, symptom, logs, note=""):
    return (f"Service: {n}\nService map (containers reach each other by hostname): catalog:9101, "
            f"inventory:9102, cart:9103, payments:9104, orders:9105.\n\n"
            f"Failing probe:\n{symptom}\n\nRecent logs:\n{logs.strip() or '(none)'}\n"
            f"{note}\nSource of main.py:\n{code}")


def wait_healthy(n, seconds=14):
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(1.5)
        ok, detail, _, _ = probe_once(n)
        if ok:
            return True, ""
    return False, detail


def run_fix(n, mode):
    s = svcs[n]
    t0 = time.time()
    before = live_file(n).read_text(encoding="utf-8")
    try:
        if mode == "golden" or not keys.live:
            if mode != "golden":
                log("warn", "No Groq key configured, restoring the known-good version instead.", n)
            shutil.copyfile(GOLDEN / n / "main.py", live_file(n))
            write_live(n, live_file(n).read_text(encoding="utf-8"))
            ok, _ = wait_healthy(n)
            s["bug"] = None if ok else s["bug"]
            s["last_fix"] = {"how": "golden restore", "diff": diff(before, live_file(n).read_text(encoding="utf-8")),
                             "seconds": round(time.time() - t0, 1), "tokens": 0}
            log("ok" if ok else "err", "Restored known-good version." if ok else "Restore did not recover the service.", n)
            if ok and gh_state["enabled"]:
                threading.Thread(target=resolve_incident, daemon=True, args=(
                    n, live_file(n).read_text(encoding="utf-8"), "restored the known-good version",
                    0, s["last_fix"]["seconds"], s["last_fix"]["diff"])).start()
            return

        healthy, symptom, _, _ = probe_once(n)
        if healthy:
            log("info", "Service is already healthy, nothing to repair.", n)
            return
        logs = log_tail(n)
        log("info", f"Diagnosing. Symptom: {symptom[:160]}", n)
        code, note = before, ""
        total_tokens = 0
        for attempt in (1, 2):
            try:
                new_code, meta = fixer.ask(keys, build_prompt(n, code, symptom, logs, note))
            except RuntimeError as e:
                log("err", str(e)[:300], n)
                return
            total_tokens += meta["tokens"]
            try:
                compile(new_code, "main.py", "exec")
            except SyntaxError as e:
                log("warn", f"Attempt {attempt}: model returned invalid Python ({e.msg}).", n)
                note = f"\nYour previous reply was not valid Python ({e.msg}). Return the full corrected file."
                continue
            log("info", f"Attempt {attempt}: patch from {meta['model']} via {meta['key']} key "
                        f"({meta['tokens']} tokens). Deploying.", n)
            write_live(n, new_code)
            clear_log(n)
            ok, detail = wait_healthy(n)
            if ok:
                s["bug"] = None
                s["last_fix"] = {"how": f"AI ({meta['model']}, {meta['key']} key)",
                                 "diff": diff(before, new_code), "seconds": round(time.time() - t0, 1),
                                 "tokens": total_tokens}
                log("ok", f"Fixed in {s['last_fix']['seconds']}s using {total_tokens} tokens.", n)
                if gh_state["enabled"]:
                    threading.Thread(target=resolve_incident, daemon=True, args=(
                        n, new_code, s["last_fix"]["how"], total_tokens, s["last_fix"]["seconds"],
                        s["last_fix"]["diff"])).start()
                return
            log("warn", f"Attempt {attempt}: still failing: {detail[:160]}", n)
            code, symptom, logs = new_code, detail, log_tail(n)
            note = "\nYour previous patch (the source below) did not fully fix it. Fix the remaining problem."
        log("err", "AI could not repair this service. Use 'Restore known-good' to recover.", n)
    finally:
        s["fixing"] = False


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
            text, _meta = fixer.ask(
                keys, f"Service: {n}\nFailing probe: {symptom}\nThe changed line is line {line} of main.py.\n\n"
                      f"Pull request diff:\n{diff(before, after, ('main', 'pull request'))}",
                system=REVIEW_SYSTEM, max_tokens=400, raw=True)
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


def get_svc(n):
    if n not in svcs or not svcs[n]["breakable"]:
        raise HTTPException(404, "unknown or non-breakable service")
    return svcs[n]


@app.get("/api/state")
def state():
    with lock:
        out = [{k: v for k, v in s.items() if k not in ("written_at", "raw_ok", "down", "fixing", "incident")}
               | {"deps": DEPS[n], "fixing": s["fixing"], "pr": pr_info(s)} for n, s in svcs.items()]
    github = {"configured": gh.configured, "enabled": gh_state["enabled"], "repo": gh.repo if gh.configured else ""}
    return {"services": out, "llm": keys.public(), "github": github, "events": list(events)}


def pr_info(s):
    inc = s.get("incident")
    if inc and inc.get("number"):
        return {"number": inc["number"], "url": inc["url"], "state": inc["state"]}
    return None


@app.post("/api/services/{n}/break")
def break_service(n: str, bug_id: str | None = None, report: bool = True):
    s = get_svc(n)
    if s["bug"] or s["fixing"]:
        raise HTTPException(409, "service already broken or being repaired")
    code = live_file(n).read_text(encoding="utf-8")
    options = [b for b in BUGS[n] if b["old"] in code and (bug_id is None or b["id"] == bug_id)]
    if not options:
        raise HTTPException(404, "no applicable fault")
    bug = random.choice(options)
    broken = code.replace(bug["old"], bug["new"], 1)
    write_live(n, broken)
    clear_log(n)
    s["bug"], s["last_fix"], s["incident"] = bug["id"], None, None
    log("err", f"Fault injected: {bug['title']}.", n)
    if report and gh_state["enabled"]:
        threading.Thread(target=open_incident, args=(n, bug, code, broken), daemon=True).start()
    return {"injected": bug["id"], "title": bug["title"]}


@app.post("/api/services/{n}/fix")
def fix_service(n: str, mode: str = "ai"):
    s = get_svc(n)
    if s["fixing"]:
        raise HTTPException(409, "repair already running")
    if s["status"] == "healthy" and not s["bug"] and mode != "golden":
        raise HTTPException(409, "service is healthy")
    s["fixing"] = True
    threading.Thread(target=run_fix, args=(n, mode), daemon=True).start()
    return {"started": True}


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
