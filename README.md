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
- [Testing](#testing)
- [Management commands](#management-commands)

---

## Setup

### With Docker

```bash
cp .env.example .env
docker compose up -d --wait db
python manage.py migrate
python manage.py seed_work_items
```

### Without Docker

Requires Python 3.12 and PostgreSQL. Create the database once:

```bash
createuser intake --createdb --pwprompt   # password: intake
createdb intake --owner intake
```

Then:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env
# edit .env: set DATABASE_URL=postgres://intake:intake@localhost:5432/intake

python manage.py migrate
python manage.py seed_work_items --analyse
python manage.py runserver
```

API at `http://localhost:8000/api/v1/` — interactive docs at `/api/v1/docs`.

### Quality gate

```bash
./scripts/verify.sh backend
```

Runs ruff check, ruff format --check, migration drift check, then pytest.
Set `SKIP_DB_START=1` when PostgreSQL is already running.

### Switching the AI provider

```bash
AI_PROVIDER=mock        # default — no key needed
AI_PROVIDER=anthropic   # real Claude via tool calling
AI_MODEL=claude-sonnet-5
AI_API_KEY=sk-ant-...   # in .env only; never committed
```

---

## Environment variables

Copy `.env.example` to `.env` and adjust. Never commit `.env`.

### Django core

| Variable | Default | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | *(none)* | Required in production; omit locally and a dev-only insecure key is used (fails hard when `DEBUG=False`) |
| `DJANGO_DEBUG` | `False` | Set to `True` for local development |
| `DJANGO_ALLOWED_HOSTS` | `*` | Comma-separated list of allowed hostnames |

### Database

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgres://intake:intake@localhost:5432/intake` | Full Postgres DSN; use `@db:5432` inside Docker Compose |
| `DB_CONN_MAX_AGE` | `60` | Persistent connection lifetime in seconds |

### AI provider

| Variable | Default | Description |
|---|---|---|
| `AI_PROVIDER` | `mock` | `mock` or `anthropic` |
| `AI_MODEL` | *(empty)* | Model name when `AI_PROVIDER=anthropic` (e.g. `claude-sonnet-5`) |
| `AI_API_KEY` | *(empty)* | Anthropic API key — **never commit** |
| `AI_TIMEOUT_SECONDS` | `20` | Hard timeout for a single LLM call |
| `AI_MAX_ATTEMPTS` | `5` | Maximum retry attempts per work item |

### Mock provider

| Variable | Default | Description |
|---|---|---|
| `MOCK_AI_LATENCY_MS` | `800` | Simulated response latency |
| `MOCK_AI_FAILURE_MODE` | `none` | Global failure mode: `none`, `timeout`, `malformed`, `bad_enum`, `error` |

Per-item failure modes are also available by putting a marker in the title:
`[simulate:timeout]`, `[simulate:malformed]`, `[simulate:bad_enum]`, `[simulate:error]`.

### Auth / security

| Variable | Default | Description |
|---|---|---|
| `INTAKE_API_KEY` | *(empty)* | Shared secret the external system sends as `X-API-Key`. Empty = endpoint is open (default for local demos) |

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
│   ├── settings.py       all config from env vars
│   └── urls.py
├── work_items/
│   ├── domain/           pure Python: status.py, transitions.py, errors.py
│   ├── services/         transaction boundaries: intake.py, workflow.py, analysis.py
│   ├── ai/               provider interface, mock, Anthropic adapter, parser, prompt
│   ├── api/
│   │   └── v1/           serializers.py, views.py, urls.py  ← the versioned contract
│   ├── management/commands/
│   │   ├── seed_work_items.py
│   │   └── reap_stale_analyses.py
│   ├── models.py         WorkItem, AnalysisAttempt, StatusTransition
│   └── tests/            257 tests
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml        ruff + pytest config
└── Dockerfile
```

---

## Architecture

**Layer rules** — each layer is enforced by a test (`test_layer_boundaries.py`):

- **`api/v1/`** — HTTP only: parse request, call a service, serialize, map errors.
  No business logic. The *only* versioned layer; a future v2 reuses the same services.
- **`services/`** — business logic and transaction boundaries. The only code that
  changes `WorkItem.status`, always through `workflow.transition()` (conditional UPDATE).
  Never `item.status = X; item.save()`.
- **`domain/`** — pure Python. No Django import. Status enum, transition table, domain errors.
- **`ai/`** — no Django model imports. Takes a frozen dataclass, returns validated output or
  raises a typed error. LLM output is always parsed and validated with Pydantic.
  Never called inside `transaction.atomic()`.

### Workflow

| From | To | Trigger |
|---|---|---|
| `RECEIVED` | `ANALYSING` | `POST /analyse` |
| `ANALYSING` | `READY_FOR_REVIEW` | valid AI result |
| `ANALYSING` | `FAILED` | timeout / provider error / invalid output / stale |
| `FAILED` | `ANALYSING` | `POST /retry` |
| `READY_FOR_REVIEW` | `COMPLETED` | `PATCH /status` (operator) |

Everything else is `409 INVALID_TRANSITION`. Every transition is a compare-and-set
`UPDATE … WHERE id = :id AND status = :from` — a concurrent click gets a 409, not a
silent double-write.

### LLM output is untrusted

- Validated against a Pydantic schema before writing anything.
- Unknown enum values are **rejected** (→ `FAILED`), never silently coerced.
- Written only after validation, in the same UPDATE as the status change.
- Every attempt is logged with the raw output (truncated to 4 KB) for debugging.

---

## Testing

```bash
pytest                     # all 257 tests
pytest -k test_intake      # just intake tests
SKIP_DB_START=1 pytest     # when Postgres is already running
```

Notable test coverage:

| Test | Proves |
|---|---|
| 5 threads submit the same `externalId` | Exactly one row via the DB unique constraint |
| 2 concurrent `POST /analyse` | One 200, one 409, model called once |
| All 25 `(from, to)` status pairs | Exactly the five documented transitions are allowed |
| Provider returns prose / `"SPAM"` / times out | Item ends `FAILED` with the right code |
| Reaper vs. analysis finishing first | Compare-and-set means result is never overwritten |
| `domain/` and `ai/` import scanning | Layer rules still hold |

Concurrency tests require PostgreSQL and skip on SQLite with a stated reason.

---

## Management commands

```bash
# Load ~11 demo work items (--analyse also runs triage on each)
python manage.py seed_work_items [--analyse]

# Move stale ANALYSING items (older than 2 × AI_TIMEOUT_SECONDS) to FAILED
python manage.py reap_stale_analyses
```
