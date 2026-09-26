# TriageDesk — Backend

Django 5.2 + Django REST Framework backend for the AI-Assisted Work Intake System.
Receives work items from an external system, triages them with a pluggable LLM
provider (mock by default), and exposes an operator workflow API.

> **Runs with no API key.** `AI_PROVIDER=mock` is the default. Every failure
> path — timeout, malformed output, bad enum value, provider error — is
> reachable from the seeded demo data without touching a real model.

---

## Contents

- [Setup](#setup)
- [Environment variables](#environment-variables)
- [API](#api)
- [Project layout](#project-layout)
- [Architecture](#architecture)
- [Assumptions](#assumptions)
- [Technical decisions](#technical-decisions)
- [Production considerations](#production-considerations)
- [Testing](#testing)
- [Management commands](#management-commands)
- [AI usage](#ai-usage)
- [Time spent](#time-spent)

---

## Setup

### Prerequisites

- Python 3.12
- PostgreSQL 16 (or Docker — see below)
- Node 20+ is only needed if you are also running the frontend

### Without Docker

Create the database once:

```bash
createuser intake --createdb --pwprompt   # password: intake
createdb intake --owner intake
```

Copy and configure the environment file:

```bash
cp .env.example .env
```

Minimum `.env`:

```
DJANGO_SECRET_KEY=dev-only-not-a-secret
DJANGO_DEBUG=true
DATABASE_NAME=intake
DATABASE_USER=intake
DATABASE_PASSWORD=intake
DATABASE_HOST=localhost
DATABASE_PORT=5432
```

Create the virtual environment, install dependencies, and start the server:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

python manage.py migrate
python manage.py seed_work_items      # loads ~10 demo items
                                      # add --analyse to also run AI triage on each
python manage.py runserver
```

API at `http://localhost:8000/api/v1/` — interactive docs at `/api/v1/docs`.

### Quality gate

Run from inside this directory (`Work-Intake-System-BE/`):

```bash
../scripts/verify.sh backend
```

Runs ruff check → ruff format --check → migration drift check → pytest.
Set `SKIP_DB_START=1` when PostgreSQL is already running.

### Switching the AI provider

```bash
# Default — deterministic, no API key needed
AI_PROVIDER=mock

# Real Claude via the Anthropic API
AI_PROVIDER=anthropic
AI_MODEL=claude-sonnet-4-6
AI_API_KEY=sk-ant-...          # in .env only; never committed

# NVIDIA NIM endpoint
AI_PROVIDER=nvidia
AI_MODEL=meta/llama-3.1-70b-instruct
AI_API_KEY=nvapi-...           # get keys at build.nvidia.com

# LangChain (routes to anthropic / openai / nvidia)
AI_PROVIDER=langchain
LANGCHAIN_BACKEND=anthropic    # anthropic | openai | nvidia
AI_MODEL=claude-sonnet-4-6
AI_API_KEY=sk-ant-...
```

`AI_FALLBACK_TO_MOCK=true` silently falls back to the mock on any provider
failure — useful in dev when an API key is absent.

---

## Environment variables

Copy `.env.example` to `.env` and adjust. Never commit `.env`.

### Django core

| Variable | Default | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | *(none)* | Required in production; omit locally and a dev-only insecure key is used (fails hard when `DJANGO_DEBUG=false`) |
| `DJANGO_DEBUG` | `false` | Set `true` for local development |
| `DJANGO_ALLOWED_HOSTS` | `*` | Comma-separated list of allowed hostnames |
| `CORS_ALLOWED_ORIGINS` | *(none)* | Comma-separated origins; only needed when `DJANGO_DEBUG=false` |

### Database

| Variable | Default | Description |
|---|---|---|
| `DATABASE_NAME` | *(required)* | PostgreSQL database name |
| `DATABASE_USER` | *(required)* | PostgreSQL user |
| `DATABASE_PASSWORD` | *(required)* | PostgreSQL password |
| `DATABASE_HOST` | `localhost` | Host; use `db` inside Docker Compose |
| `DATABASE_PORT` | `5432` | PostgreSQL port |

### AI provider

| Variable | Default | Description |
|---|---|---|
| `AI_PROVIDER` | `mock` | `mock` \| `anthropic` \| `nvidia` \| `langchain` |
| `AI_MODEL` | *(empty)* | Model name, e.g. `claude-sonnet-4-6` for Anthropic |
| `AI_API_KEY` | *(empty)* | Provider API key — **never commit** |
| `AI_FALLBACK_TO_MOCK` | `false` | `true` silently falls back to mock on any provider failure |
| `LANGCHAIN_BACKEND` | `anthropic` | LangChain routing target: `anthropic` \| `openai` \| `nvidia` |
| `AI_TIMEOUT_SECONDS` | `20` | Hard timeout for a single LLM call |
| `AI_MAX_ATTEMPTS` | `5` | Maximum retry attempts per work item |

### Mock provider

| Variable | Default | Description |
|---|---|---|
| `MOCK_AI_LATENCY_MS` | `800` | Simulated response latency |
| `MOCK_AI_FAILURE_MODE` | `none` | `none` \| `timeout` \| `malformed` \| `bad_enum` \| `error` |

Per-item failure modes are also available by putting a marker in the title:
`[simulate:timeout]`, `[simulate:malformed]`, `[simulate:bad_enum]`, `[simulate:error]`.
This is what `seed_work_items` uses.

### Auth / security

| Variable | Default | Description |
|---|---|---|
| `INTAKE_API_KEY` | *(empty = open)* | Shared secret the external system sends as `X-API-Key`. Empty = endpoint is open (default for local demos) |
| `JWT_ACCESS_MINUTES` | `60` | Operator access token lifetime |
| `JWT_REFRESH_DAYS` | `7` | Operator refresh token lifetime |
| `JWT_INVALIDATE_ON_REFRESH` | `false` | `true` invalidates the previous refresh token on every rotation |

### Cache

| Variable | Default | Description |
|---|---|---|
| `CACHE_BACKEND` | `locmem.LocMemCache` | Full Django cache backend path |
| `CACHE_LOCATION` | *(empty)* | e.g. `redis://localhost:6379/1` for Redis |

### Email

Only read when `DJANGO_DEBUG=false`; in development Django prints emails to the console.

| Variable | Default | Description |
|---|---|---|
| `EMAIL_HOST` | *(empty)* | SMTP server host |
| `EMAIL_PORT` | `587` | SMTP port |
| `EMAIL_HOST_USER` | *(empty)* | SMTP username |
| `EMAIL_HOST_PASSWORD` | *(empty)* | SMTP password |
| `EMAIL_USE_TLS` | `true` | |
| `DEFAULT_FROM_EMAIL` | `noreply@workintake.local` | |
| `FRONTEND_URL` | `http://localhost:5173` | Included in password-reset email links |

### Logging

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Root log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## API

Base path `/api/v1`. Interactive docs: `/api/v1/docs`. OpenAPI schema: `/api/v1/schema`.

| Method & path | Purpose | Success | Errors |
|---|---|---|---|
| `POST /work-items` | Idempotent intake | 201 new / 200 replay | 400, 409 `DUPLICATE_CONFLICT` |
| `GET /work-items?status=FAILED&page=1&pageSize=20` | List, filter, paginate | 200 | 400 |
| `GET /work-items/{id}` | Detail + attempts + transitions | 200 | 404 |
| `POST /work-items/{id}/analyse` | Trigger AI triage (from `RECEIVED`) | 200 | 404, 409 |
| `POST /work-items/{id}/retry` | Re-analyse (from `FAILED`) | 200 | 404, 409 `NOT_RETRYABLE` |
| `PATCH /work-items/{id}/status` | `{"status": "COMPLETED"}` | 200 | 400, 404, 409 `INVALID_TRANSITION` |

All errors use one envelope:

```json
{
  "error": {
    "code": "INVALID_TRANSITION",
    "message": "Cannot move work item from COMPLETED to ANALYSING.",
    "details": { "currentStatus": "COMPLETED" }
  }
}
```

`/analyse` returns **200 even when the AI fails** — the item is `FAILED` with an
error code, not a server error.

### Intake authentication

`POST /work-items` requires `X-API-Key: <INTAKE_API_KEY>` when `INTAKE_API_KEY`
is set. Comparison is constant-time. Leave `INTAKE_API_KEY` empty to keep the
endpoint open for local demos.

---

## Project layout

```
Work-Intake-System-BE/
├── config/
│   ├── settings.py       all config from env vars (no secret literals)
│   └── urls.py
├── common/               shared base models, mixins, JWT auth utilities
├── users/                User model and auth endpoints (login, token refresh)
├── work_items/
│   ├── domain/           pure Python: status.py, transitions.py, errors.py
│   ├── services/         transaction boundaries: intake.py, workflow.py, analysis.py
│   ├── ai/               provider protocol, mock, Anthropic/NVIDIA/LangChain adapters,
│   │                     parser.py, prompt.py, factory.py
│   ├── api/
│   │   └── v1/           serializers.py, views.py, urls.py  ← the versioned contract
│   ├── management/commands/
│   │   ├── seed_work_items.py
│   │   └── reap_stale_analyses.py
│   ├── models.py         WorkItem, AnalysisAttempt, StatusTransition
│   └── tests/
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml        ruff + pytest config
└── Dockerfile
```

---

## Architecture

**Layer rules** — each enforced by `tests/test_layer_boundaries.py`:

- **`api/v1/`** — HTTP only: parse request, call a service, serialize, map errors.
  No business logic. The *only* versioned layer; a future v2 reuses the same services.
- **`services/`** — business logic and transaction boundaries. The only code that
  changes `WorkItem.status`, always through `workflow.transition()` (compare-and-set UPDATE).
  Never `item.status = X; item.save()`.
- **`domain/`** — pure Python. No Django import. Status enum, transition table, domain errors.
- **`ai/`** — no Django model imports. Takes a frozen dataclass, returns validated output or
  raises a typed error. LLM output is always validated with Pydantic. Never called inside
  `transaction.atomic()`.

### Workflow

| From | To | Trigger | Actor |
|---|---|---|---|
| `RECEIVED` | `ANALYSING` | `POST /analyse` | system |
| `ANALYSING` | `READY_FOR_REVIEW` | valid AI result | system |
| `ANALYSING` | `FAILED` | timeout / provider error / invalid output / stale | system |
| `FAILED` | `ANALYSING` | `POST /retry` | system |
| `READY_FOR_REVIEW` | `COMPLETED` | `PATCH /status` | operator |

Everything else is `409 INVALID_TRANSITION`. `ANALYSING` and `READY_FOR_REVIEW`
are system-only — an operator cannot PATCH an item into them directly.

Every transition is a compare-and-set `UPDATE … WHERE id = :id AND status = :from`:
a concurrent click gets a 409, not a silent double-write.

### LLM output is untrusted

- Validated against a Pydantic schema before writing anything.
- Unknown enum values are **rejected** (→ `FAILED`), never silently coerced.
- Written only after validation, in the same UPDATE as the status change.
- Every attempt is logged with the raw output (truncated to 4 KB) for debugging.
- Never called inside a database transaction.

---

## Assumptions

These were taken as given when designing the data model and workflow:

- **`externalId` is the idempotency key.** The external system is responsible for keeping it stable and unique. Two requests with the same `externalId` but different payloads are a 409 `DUPLICATE_CONFLICT`, not a silent update.
- **Work items are immutable after intake.** Title and description never change; only status and analysis fields advance.
- **Analysis is operator-triggered.** `POST /work-items` receives the item; the operator (or an automated script) calls `POST /analyse` when ready. Automatic analysis on intake was considered but left out — a background worker can be added later without changing the state machine.
- **`FAILED` means an AI-processing failure only.** Validation errors during intake return 400 immediately and never create a row.
- **Single operator role, no per-user authorisation.** Any authenticated operator can transition any item. Fine-grained RBAC was out of scope for this assessment.
- **Category and priority enums are our own definition.** The external system sends free-text; the LLM maps to these enums. Unknown values from the LLM are rejected (→ `FAILED`), not coerced.
- **Retry cap.** A hard cap of `AI_MAX_ATTEMPTS` (default 5) prevents runaway retries on a persistently broken provider. Items that exceed the cap become permanently `FAILED`.
- **All timestamps are UTC.** Clients convert to local time; the server never does.

---

## Technical decisions

### 1. Database unique constraint + `get_or_create` over check-then-insert

Two concurrent `POST /work-items` requests with the same `externalId` will both pass an application-level "does it exist?" check before either inserts — a classic race condition. The fix is to let the database be the single arbiter: `get_or_create` issues an `INSERT … ON CONFLICT` under the hood, and the `uniq_work_item_external_id` constraint makes the loser get back the existing row rather than an integrity error. The five-thread concurrency test (`test_five_simultaneous_submissions_create_exactly_one_row`) exercises this path against real PostgreSQL.

### 2. Explicit state machine with compare-and-set transitions

Status transitions are a conditional `UPDATE … WHERE id = :id AND status = :from_status`. Zero rows updated means another request won the race (or the item was already moved), and a 409 is returned immediately. This prevents double-writes without advisory locks or serialisable transactions. A database `CHECK` constraint (`work_item_reviewable_has_analysis`) adds a second backstop: a row in `READY_FOR_REVIEW` or `COMPLETED` without a full analysis cannot exist at the storage layer, regardless of application code.

### 3. LLM output treated as untrusted input

The LLM response goes through a strict Pydantic schema before any database write happens. Unknown enum values are rejected outright (→ `FAILED + retry`) rather than silently defaulted — the PLAN explicitly forbids inventing an `OTHER` bucket. The result fields are written in the *same* `UPDATE` as the status change, so status and analysis data are always consistent. Every attempt is logged with the raw output (truncated to 4 KB) to aid debugging. The provider call is deliberately placed *outside* any `transaction.atomic()` block to avoid holding a database lock across a network call.

---

## Production considerations

**Authentication and authorisation** — Replace the current shared `INTAKE_API_KEY` with mTLS or HMAC-signed requests for the external system ingestion endpoint, and OAuth 2.0 / OIDC (e.g. Auth0 or an internal IdP) for operator login. Add role-based permissions (read-only analyst, full operator, admin) once there is more than one role.

**Background processing** — Move `POST /analyse` to an async worker (Celery + Redis or a managed queue). The service layer already separates the database transaction from the LLM call, so wiring it to a task queue is mostly plumbing. The queue should support per-item retry with exponential backoff and a dead-letter queue for items that exhaust retries.

**Observability** — Structured JSON logs with a correlation ID propagated from intake through analysis. Metrics: LLM call latency (p50/p99), token usage and cost per item, FAILED rate by error code, queue depth. Alerts on FAILED spikes and on analysis lag (items stuck in `ANALYSING` longer than `2 × AI_TIMEOUT_SECONDS`). Distributed tracing (OpenTelemetry) across the API → service → LLM boundary.

**LLM reliability and cost** — Per-item attempt caps (already in place). Model fallback chain: primary model → cheaper/faster fallback → mock on complete outage. Circuit breaker to stop hammering a provider that is returning errors. Prompt versioning so a prompt change can be A/B tested against the eval set before rollout. Caching of identical prompts (same title + description) to avoid redundant API calls on retries.

**Security** — PII/sensitive-data redaction before sending content to the LLM, with a clear data-retention policy and provider DPA in place. Prompt-injection hardening (treat `title` and `description` as data, not instructions). Rate limiting on the intake endpoint per source system. Secret rotation without downtime (read `INTAKE_API_KEY` from a secrets manager, not env vars baked into the image).

**Database** — Managed PostgreSQL (RDS / Cloud SQL) with automated backups and point-in-time recovery. Read replicas for the list/filter endpoint. Archival of `AnalysisAttempt` rows older than a retention window (they can be large with 4 KB raw output). Partitioning `WorkItem` by `created_at` if volume grows significantly.

**API lifecycle** — `Deprecation` / `Sunset` headers on `v1` endpoints once `v2` exists. Per-client version-usage metrics to know when it is safe to remove a version.

---

## Testing

Run from inside `Work-Intake-System-BE/` so python-dotenv loads the local `.env`:

```bash
.venv/bin/python -m pytest              # all tests
.venv/bin/python -m pytest -k intake   # just intake tests
SKIP_DB_START=1 .venv/bin/python -m pytest   # when Postgres is already running
```

Notable test coverage:

| Test | Proves |
|---|---|
| 5 threads submit the same `external_id` | Exactly one row via the DB unique constraint |
| 2 concurrent `POST /analyse` | One 200, one 409, model called once |
| All 25 `(from, to)` status pairs | Exactly the five documented transitions are allowed |
| Provider returns prose / `"SPAM"` / times out | Item ends `FAILED` with the right code |
| Reaper vs. analysis finishing first | Compare-and-set means result is never overwritten |
| `domain/` and `ai/` import scanning | Layer rules still hold |

Concurrency tests require PostgreSQL and skip on SQLite with a stated reason.

---

## Management commands

```bash
# Load ~10 demo work items; --analyse also runs AI triage on each
python manage.py seed_work_items [--analyse]

# Move stale ANALYSING items (older than 2 × AI_TIMEOUT_SECONDS) to FAILED
python manage.py reap_stale_analyses

# Generate RSA key pair for JWT_ALGORITHM=RS256 (writes to key-files/)
python manage.py generate_rsa_keys
```

