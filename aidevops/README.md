# ReviewMind AIDevOps

A containerised shop built from microservices that you can **break and repair from a dashboard**.
Repairs are done by an LLM (Groq) that sees only the failing probe, the logs and one small file.

```
 browser ──> gateway :8080 ──> catalog :9101
 (storefront)                ├> inventory :9102
                             ├> cart :9103 ──> catalog
                             ├> payments :9104
                             └> orders :9105 ──> cart, inventory, payments

 ops control plane :9000  ── probes every service, injects faults, calls Groq, redeploys fixes
```

## Run

```bash
cp ../.env.example ../.env        # then paste your Groq keys (see below)
docker compose up --build
```

| URL | What |
|---|---|
| http://localhost:9000 | Control plane: break / fix / restore, live activity feed |
| http://localhost:8080 | Storefront: watch it degrade when a service is broken |

## Keys (all in `../.env`)

```
GROQ_API_KEY=...      primary
GROQ_API_KEY_2=...    first fallback
GROQ_API_KEY_3=...    second fallback
```

For each repair the fixer tries the primary key, then fallback 1, then fallback 2; per key it tries
`openai/gpt-oss-120b`, then `openai/gpt-oss-20b` (override with `GROQ_FIX_MODELS`). With no key set,
"Fix with AI" falls back to restoring the known-good file, so the demo never dead-ends.

## How it works

- Every service is one small `main.py` (about 40 lines), so a repair costs roughly 1-2k tokens.
- Services run `uvicorn --reload` from a shared Docker volume. **Breaking or fixing a service is
  rewriting its file**; the container reloads itself. No Docker socket is mounted.
- `ops/bugs.py` holds 15 one-line faults (typos, wrong units, a 1000% discount, a wrong port,
  a syntax error that stops a service booting). The fixer never sees this file.
- `ops/probes.py` are black-box checks. A failure message says what was expected and what came
  back, and that is the only symptom the LLM gets.
- If a service fails only because something it depends on is broken, it is shown as **blocked**,
  so the AI is pointed at the real root cause.
- A patch must compile, redeploy and pass the probe within ~14 s. Otherwise one retry with the new
  symptom, then the repair is reported as failed.

## GitHub incident reports (optional)

With `GITHUB_TOKEN` and `GITHUB_REPO` set in `../.env`, every fault becomes a pull request:

1. **Break** opens a branch holding the broken `main.py` and a PR titled `[Incident] <service>`, with the failing probe in the body.
2. An LLM reviews the diff and posts an **inline review comment on the faulty line** saying what to change.
3. **Fix** (AI or known-good restore) commits the repair to that branch, posts a "Resolved" comment (time, tokens, diff) and **closes the PR without merging**.

Toggle it from the GitHub chip in the dashboard header. It is off when no token is set. The token needs a fine-grained PAT on the repo with *Contents: read/write* and *Pull requests: read/write*. The repo must already contain `aidevops/golden/<service>/main.py` on its default branch (push first).

```
GITHUB_TOKEN=github_pat_...
GITHUB_REPO=divyammadaan/ReviewMind--AIDevOps
GITHUB_BASE_BRANCH=main            # optional
```

## Tests

With the stack running and a key in `../.env`:

```bash
python tests/repair_all.py            # inject all 15 faults, AI-repair each, report pass/fail + tokens
python tests/repair_all.py --mode golden   # same, but restore known-good (no API calls)
python tests/repair_all.py --only cart,orders
```

The incident-PR flow is tested against a fake GitHub, so no real repo is touched:

```bash
python tests/mock_github.py &                                   # fake GitHub on :9999
docker compose -f docker-compose.yml -f tests/docker-compose.mock.yml up -d ops
python tests/github_flow.py                                     # 12 checks, end to end
docker compose up -d --force-recreate ops                       # back to the real config
```

`repair_all.py` passes `report=false`, so running it never opens real PRs.

## Layout

```
golden/<service>/main.py   known-good code (also the restore point)
golden/gateway/            edge proxy + storefront UI
ops/                       control plane: main.py, probes.py, bugs.py, fixer.py, static/index.html
```
