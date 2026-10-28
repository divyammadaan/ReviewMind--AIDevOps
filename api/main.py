"""ReviewMind demonstration server.

Serves two things for the project demonstration:

  /          a results dashboard -- the experiment, the ablation, the costs, and
             the three validity threats we found in our own method
  /demo      a live review page -- paste a code change, watch ReviewMind
             retrieve context and report defects

This also closes a gap against the original proposal, which specified FastAPI as
the backend orchestrating fetch -> retrieve -> review but had no exposed service
layer until now.

Run:
    .venv/bin/uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import glob
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from reviewmind.arms import llm_arm, static_arm  # noqa: E402
from reviewmind.parsing.chunker import chunk_repo  # noqa: E402
from reviewmind.retrieval.graph import GraphRetriever  # noqa: E402
from reviewmind.review.llm import build_llm  # noqa: E402

app = FastAPI(title="ReviewMind", version="0.2.0-a2")
templates = Jinja2Templates(directory=str(ROOT / "api" / "templates"))

# The LIVE DEMO runs against the Checkout API corpus: a small, readable service
# whose defects require CROSS-FILE context, which is the capability being shown. The EXPERIMENT ran
# against `taskapi`, which is more realistic; its results are what the dashboard
# reports. The two are kept separate on purpose and the distinction is stated in
# docs/TOOL_COVERAGE.md -- presenting demo code as experiment code would
# misrepresent the evaluation.
CORPUS = ROOT / "experiments" / "corpus" / "shop"
EXPERIMENT_CORPUS = ROOT / "experiments" / "corpus" / "taskapi"
DATASET = ROOT / "experiments" / "dataset" / "cases"
DEMO_EXAMPLES = ROOT / "experiments" / "demo_examples"

# Built once at startup: indexing on every request would make the live demo feel
# slow for reasons unrelated to the thing being demonstrated.
_retriever: Optional[GraphRetriever] = None


def retriever() -> GraphRetriever:
    global _retriever
    if _retriever is None:
        _retriever = GraphRetriever(chunk_repo(CORPUS))
    return _retriever


# --------------------------------------------------------------------------- #
# Results loading
# --------------------------------------------------------------------------- #

def latest_summary() -> dict[str, Any]:
    """The run the dashboard reports.

    RESULTS_RUN pins a specific run. Without it, the most recent valid run is
    used. Pinning matters because the dashboard and the written report must cite
    the SAME run: hosted inference is not bitwise deterministic even at
    temperature 0, so two runs under identical conditions differed by one case
    on the diff-only arm (11/14 vs 12/14). Showing one number on a slide and
    another on screen would look like an inconsistency rather than variance.

    Runs flagged valid_for_reporting=false (mock backend) are always skipped, so
    pipeline-test output can never be displayed as a result.
    """
    pinned = os.getenv("RESULTS_RUN", "").strip()
    if pinned:
        f = ROOT / "results" / pinned / "summary.json"
        if f.exists():
            try:
                data = json.loads(f.read_text())
                if data.get("config", {}).get("valid_for_reporting"):
                    return data
            except (OSError, json.JSONDecodeError):
                pass

    for path in sorted(glob.glob(str(ROOT / "results" / "run_*" / "summary.json")), reverse=True):
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("config", {}).get("valid_for_reporting"):
            if len(data.get("arms", {})) >= 3:
                return data
    return {}


def load_json(rel: str) -> dict[str, Any]:
    p = ROOT / rel
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def demo_cases() -> list[dict[str, Any]]:
    """One-click examples for the live demo page.

    These are written against the simple `school` corpus and phrased in plain
    language, because a demonstration fails if the presenter cannot read the
    code aloud. Each one is a pure addition whose defect is only visible from
    another file -- which is exactly the capability being demonstrated.
    """
    out = []
    for f in sorted(DEMO_EXAMPLES.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "case_id": d["id"],
            "title": d.get("title", ""),
            "description": d.get("plain", ""),
            "cross_file": True,
            "injection_mode": "addition",
            "diff": d["diff"],
        })
    return out


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    summary = latest_summary()
    arms = summary.get("arms", {})
    config = summary.get("config", {})

    def arm(name: str) -> dict[str, Any]:
        a = arms.get(name, {})
        return {
            "recall": a.get("recall", 0) * 100,
            "semantic": a.get("recall_semantic", 0) * 100,
            "ci": [round(x * 100) for x in a.get("recall_semantic_ci95", [0, 0])],
            "precision": a.get("precision_lower_bound", 0) * 100,
            "tokens": round(a.get("total_tokens", 0) / max(1, a.get("n_cases", 1))),
            "latency": a.get("median_latency_s", 0),
            "by_mode": a.get("semantic_by_injection_mode", {}),
            "by_class": a.get("recall_by_class", {}),
            "n_cases": a.get("n_cases", 0),
        }

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "static": arm("static"),
        "llm_diff": arm("llm_diff"),
        "llm_rag": arm("llm_rag"),
        "config": config,
        "ablation": load_json("results/retrieval_ablation.json"),
        "sourcegraph": load_json("results/sourcegraph_navigation.json"),
        "run_name": summary.get("run", "n/a"),
    })


@app.get("/demo", response_class=HTMLResponse)
def demo(request: Request):
    return templates.TemplateResponse("demo.html", {
        "request": request,
        "cases": demo_cases(),
    })


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

class ReviewRequest(BaseModel):
    diff: str
    use_retrieval: bool = True
    changed_file: Optional[str] = None


@app.post("/api/review")
def api_review(req: ReviewRequest):
    """Review a diff. `use_retrieval=False` reproduces the diff-only arm.

    Exposing both arms here is intentional: it lets a demonstration show the
    SAME change reviewed with and without retrieval, which is the project's
    central comparison, rather than asking the audience to take it on trust.
    """
    if not req.diff.strip():
        return JSONResponse({"error": "empty diff"}, status_code=400)

    changed = req.changed_file
    if not changed:
        for line in req.diff.splitlines():
            if line.startswith("+++ b/"):
                changed = line[6:].strip()
                break

    started = time.perf_counter()
    context: list[dict[str, Any]] = []

    if req.use_retrieval:
        r = retriever()
        src_path = CORPUS / changed if changed else None
        hits = r.search(
            req.diff, top_k=6,
            exclude_files=[changed] if changed else [],
            changed_file_source=(src_path.read_text() if src_path and src_path.exists() else None),
            changed_file=changed,
        )
        context = [{
            "file": h.chunk.file, "name": h.chunk.name,
            "start_line": h.chunk.start_line, "end_line": h.chunk.end_line,
            "direction": getattr(h, "direction", "lexical"),
            "text": h.chunk.text[:600],
        } for h in hits]
        result = llm_arm.review("live", req.diff, build_llm(), retriever=r,
                                repo_dir=CORPUS, changed_file=changed, top_k=6)
    else:
        result = llm_arm.review("live", req.diff, build_llm())

    payload = result.to_dict()
    payload["context"] = context
    payload["wall_s"] = round(time.perf_counter() - started, 2)
    return payload


@app.post("/api/static")
def api_static(req: ReviewRequest):
    """Run the static-analysis baseline (ruff + bandit) for side-by-side demos."""
    changed = req.changed_file
    if not changed:
        for line in req.diff.splitlines():
            if line.startswith("+++ b/"):
                changed = line[6:].strip()
                break
    if not changed:
        return JSONResponse({"error": "could not determine changed file"}, status_code=400)
    res = static_arm.review("live", CORPUS, [changed])
    return res.to_dict()


class AskRequest(BaseModel):
    question: str


@app.post("/api/ask")
def api_ask(req: AskRequest):
    """Code Q&A over the corpus."""
    from reviewmind.qa.bot import CodeQABot
    bot = CodeQABot(CORPUS, build_llm())
    return bot.ask(req.question).to_dict()


# --------------------------------------------------------------------------- #
# Demo control panel
#
# Drives the whole demonstration from the browser so nothing has to be typed.
# Every action is scoped to the demo corpus (experiments/corpus/shop) and
# refuses to touch anything outside it -- see _demo_file().
# --------------------------------------------------------------------------- #

SHOP = ROOT / "experiments" / "corpus" / "shop"

# The defect, and the canonical repair.
#
# Detection must NOT rely on matching the repaired line, because the fix is
# GENERATED: the model may write any correct implementation. It has produced
# both `apply_discount(amount, tier)` and `amount * (1 - percent / 100)` --
# equally correct, but only one matches a hardcoded string. State is therefore
# decided by BEHAVIOUR (what the shop actually charges), with the defect pattern
# as a fast path. Anything else is reported honestly as unknown.
BROKEN_LINE = "    payable = amount * percent"
FIXED_LINE = "    payable = apply_discount(amount, tier)"

# Where the demo shop is reachable. Localhost when run from a virtualenv;
# a service name when run under Docker Compose, where 127.0.0.1 would resolve
# to the API container itself rather than the shop.
SHOP_URL = os.getenv("SHOP_URL", "http://127.0.0.1:9000")

# A Rs.1000 order for a gold (20% off) customer.
DEMO_AMOUNT = 1000.0
EXPECTED_TOTAL = 800.0
BROKEN_TOTAL = 20000.0


def _shop_charge() -> float | None:
    """What the live shop page charges for the reference order."""
    import re

    import requests as _rq
    try:
        html = _rq.get(f"{SHOP_URL}/shop?customer=101&amount={DEMO_AMOUNT:.0f}",
                       timeout=4).text
        m = re.search(r'class="amt">&#8377;([\d,]+\.\d{2})', html)
        return float(m.group(1).replace(",", "")) if m else None
    except Exception:  # noqa: BLE001 -- the shop may simply not be running
        return None


REFERENCE_FILE = SHOP / "_reference_service.py"


def _demo_file() -> Path:
    """The only file the control panel may modify."""
    f = (SHOP / "service.py").resolve()
    if not str(f).startswith(str(SHOP.resolve())):
        raise RuntimeError("refusing to touch a file outside the demo corpus")
    return f


def _shop_state() -> str:
    """Broken or working, judged by what the shop charges."""
    try:
        text = _demo_file().read_text()
    except OSError:
        return "unknown"

    # Fast path: the known defect is present verbatim.
    if BROKEN_LINE in text:
        return "broken"

    # Otherwise decide on behaviour, which is robust to any correct fix.
    charge = _shop_charge()
    if charge is None:
        return "working" if FIXED_LINE in text else "unknown"
    if abs(charge - EXPECTED_TOTAL) < 0.01:
        return "working"
    if charge > EXPECTED_TOTAL:
        return "broken"
    return "unknown"


def _read_token() -> str:
    """GitHub token, from the environment or the file the demo scripts use."""
    import os as _os
    tok = _os.getenv("GITHUB_TOKEN", "").strip()
    if tok:
        return tok
    try:
        return (Path.home() / ".rm_token").read_text().strip()
    except OSError:
        return ""


@app.get("/control", response_class=HTMLResponse)
def control(request: Request):
    import os as _os
    return templates.TemplateResponse("control.html", {
        "request": request,
        "state": _shop_state(),
        "repo": _os.getenv("GITHUB_REPO", "Deadmaus07/ReviewMind"),
        # Configurable so a fresh demo PR does not require a code change.
        "demo_pr": int(_os.getenv("DEMO_PR", "8")),
    })


@app.get("/api/demo/status")
def demo_status():
    """Current state of the demo service, and what the SHOP PAGE charges.

    Reads the customer-facing page rather than /checkout, because /checkout is
    not the endpoint the demonstration breaks -- reporting its (correct) total
    while the shop overcharges would be actively misleading on the panel.
    """
    # One implementation, in _shop_charge(). A second copy here was hardcoded to
    # 127.0.0.1 and silently reported the shop as down under Docker Compose,
    # where the shop is a separate container.
    total = _shop_charge()
    return {"state": _shop_state(), "shop_up": total is not None,
            "checkout_total": total}


@app.post("/api/demo/break")
def demo_break():
    """Reintroduce the defect so the demonstration can be repeated.

    Restores the file from a pristine reference FIRST, then applies the one-line
    defect. Editing in place does not survive repeated fix/break cycles: the fix
    is generated, so each one may restructure the handler differently. One run
    inlined discount_percent() and removed the `percent` variable; the next
    Break then wrote `payable = amount * percent`, referencing a name that no
    longer existed, and the page returned HTTP 500 instead of the overcharge.

    Resetting from a known-good copy makes the demonstration repeatable no
    matter what shape the previous fix left behind.
    """
    f = _demo_file()
    if not REFERENCE_FILE.exists():
        return JSONResponse(
            {"ok": False, "error": "reference copy missing"}, status_code=500)

    pristine = REFERENCE_FILE.read_text()
    if FIXED_LINE not in pristine:
        return JSONResponse(
            {"ok": False, "error": "reference copy does not contain the expected line"},
            status_code=500)

    f.write_text(pristine.replace(FIXED_LINE, BROKEN_LINE, 1))
    return {"ok": True, "state": "broken", "reset_from_reference": True}


@app.post("/api/demo/fix")
def demo_fix():
    """Ask the model to write a fix, then apply it.

    This genuinely calls the LLM rather than restoring a stored copy: the point
    of the demonstration is that the fix is generated, not replayed.
    """
    from reviewmind.automation.issue_to_pr import propose_patch

    issue = ("the checkout page overcharges: payable is calculated as amount "
             "times percent, but discount_percent returns a percentage like 20 "
             "meaning 20 percent off, not a multiplier")
    try:
        prop = propose_patch(0, "checkout page overcharges", issue,
                             SHOP, build_llm(), scoped_root=True)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                            status_code=500)

    applied = []
    rejected = [{"path": c.path, "reason": c.rejected_reason}
                for c in prop.changes if not c.accepted]
    for c in prop.accepted_changes:
        target = (SHOP / c.path).resolve()
        if not str(target).startswith(str(SHOP.resolve())):
            rejected.append({"path": c.path, "reason": "outside the demo corpus"})
            continue
        target.write_text(c.new_content)
        applied.append(c.path)

    return {"ok": bool(applied), "state": _shop_state(),
            "reasoning": prop.reasoning, "applied": applied,
            "rejected": rejected, "errors": prop.errors,
            "tests": [t.get("name") for t in prop.test_suggestions][:3],
            "latency_s": round(prop.latency_s, 2)}


class ReviewPRRequest(BaseModel):
    pr: int
    post: bool = True


@app.post("/api/demo/review")
def demo_review_pr(req: ReviewPRRequest):
    """Review a live pull request and optionally post the comments."""
    import os as _os

    import requests as _rq

    from reviewmind.github.client import post_review

    token = _read_token()
    repo = _os.getenv("GITHUB_REPO", "Deadmaus07/ReviewMind")
    if not token:
        return JSONResponse({"ok": False, "error": "no GitHub token available"},
                            status_code=400)
    try:
        r = _rq.get(f"https://api.github.com/repos/{repo}/pulls/{req.pr}",
                    headers={"Authorization": f"Bearer {token}",
                             "Accept": "application/vnd.github.v3.diff"}, timeout=30)
        r.raise_for_status()
        diff = r.text
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"could not fetch PR: {exc}"},
                            status_code=502)

    changed = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            changed = line[6:].strip().split("/")[-1]
            break

    result = llm_arm.review(f"pr-{req.pr}", diff, build_llm(),
                            retriever=GraphRetriever(chunk_repo(SHOP)),
                            repo_dir=SHOP, changed_file=changed, top_k=6)

    posted = None
    if req.post and not result.errors:
        p = post_review(repo, req.pr, token, result.issues,
                        result.test_suggestions, result.model,
                        result.retriever, dry_run=False)
        posted = {"inline": p.posted_inline, "summary": p.posted_summary,
                  "skipped": p.skipped, "errors": p.errors}

    payload = result.to_dict()
    payload["posted"] = posted
    payload["pr_url"] = f"https://github.com/{repo}/pull/{req.pr}"
    payload["ok"] = not result.errors
    return payload


@app.get("/api/health")
def health():
    import os
    return {
        "ok": True,
        "chunks_indexed": len(retriever()),
        "llm_mode": os.getenv("REVIEWMIND_LLM_MODE", "mock"),
        "model": os.getenv("GROQ_MODEL", "unset"),
        "key_present": bool(os.getenv("GROQ_API_KEY", "").strip()),
    }
