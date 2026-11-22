# ReviewMind AIDevOps

A containerised shop built from microservices that you can **break and repair from a dashboard**.
A team of small LLM agents (Groq) triages the failure, writes a patch and reviews it, using the failing
probe, the logs, a distributed trace and one small file. **A human approves every patch before it touches a
running service** (from the dashboard or from a phone), a patch can be **tried on a canary first**, and a
patch that fails verification is **rolled back automatically**. Every request is traced with OpenTelemetry.

```
 browser ──> gateway :8080 ──> catalog :9101
 (storefront)                ├> inventory :9102
                             ├> cart :9103 ──> catalog
                             ├> payments :9104
                             └> orders :9105 ──> cart, inventory, payments

 <svc>-canary containers  ── a twin of every service; the gateway sends it a share of traffic during a canary
 ops control plane :9000  ── probes services, injects faults, runs the agents, approves/applies/rolls back
                              patches, serves the phone app (/m/), and is the OTLP receiver (traces/metrics/logs)
 metrics sidecar :9200    ── CPU and memory per container (the only container-engine-socket holder)
```

13 containers: gateway, 5 services, 5 canary twins, the control plane and the metrics sidecar.

## Run

```bash
cp ../.env.example ../.env        # then paste your Groq keys (see below)
docker compose up --build
```

| URL | What |
|---|---|
| http://localhost:9000 | Control plane: break, propose, approve/reject, restore; metrics; distributed traces |
| http://localhost:8080 | Storefront: watch it degrade when a service is broken |
| http://localhost:9000/m/ | Phone app (installable web app): alerts, approve or reject fixes |

**Settings** (chips in the dashboard header, also `POST /api/settings`): *Watchdog*, *Agents* and *Canary*.
Defaults: watchdog off, agents on, canary off (`WATCHDOG`, `AGENTIC`, `CANARY`, `CANARY_PERCENT`,
`CANARY_OBSERVE` in `../.env` set the starting values).

## Keys (all in `../.env`)

```
GROQ_API_KEY=...      primary
GROQ_API_KEY_2=...    first fallback
GROQ_API_KEY_3=...    second fallback
```

For each proposal the fixer tries the primary key, then fallback 1, then fallback 2; per key it tries
`openai/gpt-oss-120b`, then `openai/gpt-oss-20b` (override with `GROQ_FIX_MODELS`). With no key set,
"Fix with AI" falls back to restoring the known-good file, so the demo never dead-ends.

## The repair workflow

```
 Break ─> probe fails ─> Fix with AI ─> PENDING APPROVAL ─┬─ Reject ─────────> REJECTED (service untouched)
                          (diff shown,                    │
                           nothing applied)               └─ Approve & Apply ─> APPROVED ─> deploy ─> verify
                                                                                              │
                                                  pass: RESOLVED  <───────────────────────────┤
                                                  fail/timeout: ROLLED BACK to last known-good┘
```

1. **Propose.** *Fix with AI* generates the corrected file and a diff. **Nothing is applied.** The card
   shows the diff with **Approve & Apply** and **Reject**. The incident states are *Pending approval*,
   *Approved*, *Rejected*, *Resolved* and *Rolled back*.
2. **Approve.** Only then is the patch written to the running service. It must compile, the service must
   restart and the health probe must pass within `VERIFY_TIMEOUT` seconds (default 14).
3. **Roll back automatically.** If verification fails or times out, the last known-good code is restored,
   health is re-checked, and the reason and timestamp are shown on the card and in the activity log. A
   failed patch is **never** marked resolved. "Last known-good" is the code that was healthy before the
   fault (or the original code at start-up).
4. **Reject** discards the proposal and leaves the service exactly as it was.

Guard rails: a proposal is refused if the service changed since it was generated (stale), a patch that
does not compile is refused before it is ever held for approval, and an operator can still use
**Restore known-good**, which discards any pending proposal.

You can also submit your own patch: `POST /api/services/<svc>/propose` with `{"code": "<full main.py>"}`.
It goes through the same approval, verification and rollback. `fix?mode=ai&auto_approve=true` skips the
human step. It exists for automation and tests, is logged loudly, and is not used by the dashboard.

**Why a rollback cannot get stuck.** Each service runs under `watchfiles`, which restarts it on every
file change and escalates to SIGKILL. We found (and tested, see `workflow_test.py` 4b) that plain
`uvicorn --reload` waits forever for a process that ignores SIGTERM, so one poisonous patch would block
the rollback itself.

### GitHub incident pull requests (optional, separate from approval)

With `GITHUB_TOKEN` and `GITHUB_REPO` set in `../.env`, every fault becomes a pull request: **Break** opens
a branch holding the broken `main.py` and an `[Incident]` PR; an LLM posts an inline review comment on the
faulty line. The PR is a **record of the incident, not an approval**. The proposed diff, a rejection or a
rollback are added as comments, and the PR is closed (never merged) only when a verified fix is applied.
Toggle it with the GitHub chip in the dashboard header. The token needs a fine-grained PAT with *Contents*
and *Pull requests* read/write, and the repo must already contain `aidevops/golden/<service>/main.py`.

```
GITHUB_TOKEN=github_pat_...
GITHUB_REPO=divyammadaan/ReviewMind--AIDevOps
GITHUB_BASE_BRANCH=main            # optional
```

## The agentic workflow

With **Agents** on (the default), a repair is a small pipeline of specialised agents instead of one prompt:

| Agent | Job |
|---|---|
| **Triage** | Reads the failing check, logs and trace and says in words what is probably wrong, and the rule the code should follow *in general* (no code) |
| **Fixer** | Writes the patch, guided by the triage hypothesis |
| **Reviewer** | Judges the patch **before a human sees it**: does it fix the defect in general, or only special-case the example in the failing check? It can send the patch back for one revision |
| **Verifier** | Not an LLM: the compile + health-probe check after approval, with rollback |

Triage and Reviewer use the cheaper model first. The dashboard shows each agent's summary, tokens and
time. Agents are optional: if one fails, the pipeline carries on without it. Tested with the real model
(`agents_test.py`): the Reviewer rejects the overfitted `amount <= 100` patch we once saw the Fixer write, and
approves the correct fix. Turn agents off for a single Fixer (cheaper and faster).

## Canary releases

With **Canary** on, an approved patch is not written to the running service straight away:

1. It is deployed to the service's `-canary` twin (a separate container running the same image).
2. The gateway sends `CANARY_PERCENT` of that service's traffic to the canary for `CANARY_OBSERVE` seconds
   (`X-Served-By` on every response says which instance answered). If the canary is unreachable the gateway
   falls back to the stable instance, so a broken canary never costs a user a request.
3. The canary must come up healthy, keep passing its health checks, and (if it served at least 5 real
   requests) keep its error rate at or below 5%.
4. Pass: the patch is promoted to the stable service and verified as before. Fail: the split is closed, the
   stable service is **not touched**, and the incident shows *Canary failed*.

Limitation: the split covers traffic that comes through the gateway (the storefront). Calls between services
(for example orders to cart) go to the stable instances directly. Tested in `canary_test.py`.

## Watchdog

With **Watchdog** on, a service that stays broken or down gets a fix proposed **automatically**, once per
incident, and you are alerted. A human still approves; nothing is applied. It will not re-propose after a
rejection until the service has been healthy again.

## Phone app (browser version, optional)

A 17 KB web app served by the control plane at `/m/`: no app store, no Firebase and no build tools. It shows
service health, lists incidents, shows the proposed diff with the agents' summaries, and lets you **approve or
reject** (a deliberate two-tap confirmation, never a button on the notification itself). Alerts use **Web
Push** with VAPID keys generated on first start.

- **Security.** The phone API (`/m/api/*`) needs the access token shown in the dashboard's *Phone app* panel
  (set your own with `MOBILE_TOKEN` in `../.env`). The desktop dashboard (`/api/*`) has no login, so expose
  only `/m/` to the phone.
- **HTTPS is required** for browser alerts and installing (a browser rule), except on `localhost`, so for a
  real phone use the **native Android app** below instead; it needs no HTTPS.
- Open `http://localhost:9000/m/` on the laptop to try the browser version.
- Verified: the encrypted push messages, VAPID signature, token protection, watchdog, approve/reject
  (`mobile_test.py`) and the app in desktop Chrome.

## Simulated traffic

A **Traffic** chip on the dashboard (or `POST /api/settings?traffic_enabled=true`) starts simulated shoppers who browse,
fill carts and check out through the gateway. The rate rises and falls on its own, between about 1 and 20 requests per
second (`TRAFFIC_MAX`, default 20), with occasional bursts. It cannot overload the shop: at most 8 requests are in flight,
a request that cannot start is dropped rather than queued, and the rate falls to the minimum if the shop becomes slow or
mostly fails. Checkouts only use products 2 to 6, because product 1 is what the health probes measure. A warehouse job tops
the stock up. Off by default (`TRAFFIC=on` to start with it on). Test: `python tests/traffic_test.py`.

## Android app

A native Android app (about 28 KB, plain Java, no libraries) in `android/`. It needs no Google push service and
no HTTPS: it works over the same Wi-Fi as the laptop. Tested on an emulator and on a real Android phone.

- **Services**: live health of every service, with the Watchdog / Agents / Canary settings.
- **Incident**: what is wrong, the AI's diff, the agent steps and tokens used. **Approve** needs two taps within
  6 seconds; **Reject** discards the proposal. Nothing is applied before approval.
- **Alerts**: a background service checks every few seconds (3 / 5 / 10 / 30 s, set in Settings) and raises real
  Android notifications ("Cart is broken", "Fix ready for Cart"). Tapping a notification only opens the app.
- **Settings**: server address and token, alert switch, check interval, battery exemption, disconnect.

Install: copy `android/dist/ReviewMindOps.apk` to the phone, allow "install unknown apps", open it. Enter the laptop's
IP and port (`ipconfig`, for example `192.168.1.5:9000`) and the access token from the dashboard's Phone panel.
Windows Firewall must allow inbound TCP 9000 (admin PowerShell:
`New-NetFirewallRule -DisplayName "ReviewMind ops" -Direction Inbound -Protocol TCP -LocalPort 9000 -Action Allow`).

Build (Android Studio's bundled JDK and the SDK are enough):
`cd android; $env:JAVA_HOME="C:\Program Files\Android\Android Studio\jbr"; .\gradlew.bat assembleRelease`.

Tested on an Android 15 emulator against the live stack: connect, wrong-token and unreachable-server messages, break
a service and receive both alerts, review the diff, two-tap approve through to verified, reject, alert history.
Not yet tested on a physical phone, and Android may pause background apps unless the battery exemption is granted.

## Running on Podman

The stack is runtime-neutral: the only engine-specific part is the metrics sidecar's socket mount, set by
`CONTAINER_SOCK`. On Windows, with Podman Desktop installed and `../.env` created:

```powershell
cd aidevops
.\podman-up.ps1          # starts the Podman machine, finds its socket, runs `podman compose up --build -d`
```

By hand: `podman machine start`, then
`$env:CONTAINER_SOCK = (podman info --format "{{.Host.RemoteSocket.Path}}")` and `podman compose up --build -d`.
`podman compose` needs a compose provider (`docker-compose` or `pip install podman-compose`). Podman runs
rootless, with no always-running root daemon, which is its main advantage over Docker.

**Status: tested on Podman 5 (rootless, WSL machine, Windows 11).** All 13 containers start and the
workflow, canary, agents, telemetry, observability and phone-app suites pass with `CONTAINER_CLI=podman`
(the test scripts and `scripts/preflight.py` use that variable to pick the engine). Known differences:
- Rootless Podman reports no container memory figure, so the dashboard shows memory as "n/a" (CPU is fine).
- Windows Firewall blocks the Podman VM from reaching the host, so the phone-app test runs its stand-in push
  service inside the `ops` container (`PUSH_SINK=inside`, the default on Podman).
- `tests/docker-compose.mock.yml` (GitHub mock) uses Docker's `host-gateway`; it is not run on Podman.

## Observability (OpenTelemetry: traces, metrics and logs)

Every service and the control plane are instrumented **without editing the service code**:
`otel/sitecustomize.py` is loaded by Python in every process (`PYTHONPATH=/otel`) and instruments FastAPI
(incoming requests) and httpx (outgoing requests), and exports all three signals over OTLP/HTTP. The W3C `traceparent` header carries the trace ID
across HTTP calls, so one storefront request becomes one trace spanning gateway, orders, cart, catalog,
inventory and payments, with each span's service name, duration, HTTP status and any exception.

- **Collector.** Spans, metrics and logs are exported over OTLP/HTTP to the control plane (`POST /v1/traces`,
  `/v1/metrics`, `/v1/logs`), which stitches spans into traces. No extra container is needed. To use a real collector or Jaeger instead, set
  `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` in `docker-compose.yml`.
- **Dashboard.** *Distributed traces* lists failing traces first (with the service where the request
  actually broke), plus storefront traffic and health probes; click a trace for a waterfall. Each service
  card also shows requests/min, error % and p95 latency from its server spans (storefront traffic only).
- **Metrics.** Each service exports the request-duration histogram every 5 s. The control plane turns the
  cumulative counters into **requests/s, 5xx error %, p50/p95/p99 latency and requests in flight** per
  service over the last minute (shown in the *Metrics* panel; `GET /api/metrics`). Counter resets when a
  service restarts are detected. These numbers include the control plane's own health probes.
- **Logs.** Python logging, including uvicorn's access and error logs with full stack traces, is exported
  and shown in the *Logs* panel (warnings and errors by default; `GET /api/logs`). A log written inside a
  request is stamped with that request's **trace ID**, so opening a trace shows its log lines. Some lines
  (for example uvicorn's "Exception in ASGI application" traceback) are written after the request's span
  has ended and carry no trace ID; the dashboard shows those separately, labelled as matched by time only.
- **No feedback loop.** The control plane is the collector, so it never exports its own access logs (they
  would otherwise be received, logged and exported again forever).
- **Origin labels.** Health probes carry an `origin=probe` OpenTelemetry *baggage* item that propagates
  across services, so probe traffic never pollutes storefront numbers.
- **Used by the repair.** The newest failing trace for the broken service (for example
  *"orders → payments:9140 ConnectError"*) is added to the AI prompt as evidence.
- **Not a hard dependency.** Telemetry setup is wrapped so that any failure leaves the service running
  untelemetered; spans export from a background thread with a 2 s timeout and a bounded queue, so an
  unreachable collector cannot slow or break a request. Exporter errors are rate-limited to one per
  minute. Verified in `telemetry_test.py` and `observability_test.py` (collector stopped, services restarted). Set
  `OTEL_SDK_DISABLED=true` to turn it off.

## Live metrics per service

Each card shows live **CPU** (% of one core, with a sparkline), **memory** and **tokens** consumed by that
service, next to its probe latency and trace-derived request stats. CPU and memory come from the `metrics`
sidecar, the only container with the Docker socket mounted. It serves one read-only `/stats` endpoint. Mounting
the socket gives that one container broad access to Docker on the host, so it is kept tiny and fixed.

## How the rest works

- Every service is one small `main.py` (about 40 lines), so a proposal costs roughly 1-2k tokens.
- **Breaking or fixing a service is rewriting its file** on a shared volume; `watchfiles` restarts it. The
  control plane never touches Docker.
- `ops/bugs.py` holds 15 one-line faults; the fixer never sees this file. `ops/probes.py` are black-box
  checks whose failure message (expected vs actual) is the symptom the LLM gets.
- If a service fails only because something it depends on is broken it is shown as **blocked**.

## Tests

Everything below runs against the real stack; nothing is mocked. With the stack running and a key in `../.env`:

```bash
python tests/workflow_test.py     # approval, rejection, failed verification, timeout, poison patch, guard rails (54 checks)
python tests/telemetry_test.py    # trace propagation, failure localisation, probe labelling, collector down (27 checks)
python tests/observability_test.py # metrics vs real traffic, error rates, stack-trace logs, log/trace linking (26 checks)
python tests/agents_test.py       # Triage/Fixer/Reviewer on the real model; the Reviewer catches an overfitted patch (20 checks)
python tests/canary_test.py       # real traffic split, good patch promoted, bad patch stopped, down canary harmless (31 checks)
python tests/mobile_test.py       # phone API + token, real encrypted push, VAPID signature, watchdog, approve from phone (39 checks)
python tests/repair_all.py        # inject all 15 faults, AI-repair each (auto_approve), count only VERIFIED repairs
python tests/repair_all.py --mode golden   # same without the LLM (no API calls)
```

`workflow_test.py` deploys genuinely bad patches (one that compiles but does not fix the bug, one that hangs
at start-up, one that ignores SIGTERM) and checks the service really recovers. `telemetry_test.py` stops the
control plane (the collector) and restarts services while it is down.

The incident-PR flow is tested against a fake GitHub, so no real repo is touched:

```bash
python tests/mock_github.py &                                   # fake GitHub on :9999
docker compose -f docker-compose.yml -f tests/docker-compose.mock.yml up -d ops
python tests/github_flow.py                                     # end to end, incl. "a PR is not an approval"
docker compose up -d --force-recreate ops                       # back to the real config
```

All fault injection in the tests uses `report=false`, so running them never opens real PRs.

## Layout

```
golden/<service>/main.py   known-good code (also the restore point)
golden/gateway/            edge proxy + storefront UI
ops/                       control plane: main.py, probes.py, bugs.py, fixer.py, agents.py, canary.py, notify.py,
                           github.py, telemetry.py (traces), signals.py (metrics + logs), static/index.html
ops/static/m/              the phone app (index.html, sw.js, manifest.json, icons)
serve.sh                   how every service container starts (shared by Docker and Podman)
podman-up.ps1              one-command start on Podman (Windows)
otel/sitecustomize.py      OpenTelemetry bootstrap loaded into every process
metrics/                   CPU/memory sidecar
tests/                     end-to-end tests (see above)
```
