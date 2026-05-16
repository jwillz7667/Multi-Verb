# verbio-engine

Python 3.12 service that owns the 2 Hz tick loop, the rules engine, and the LiveKit agent join.
The web app talks to it only through Redis (commands) and Postgres (decisions / state) — never
directly. See `verbio-engineering-brief.md` §4 for the topology and §6 for the tick loop.

## Layout

```
services/engine/
├── pyproject.toml          # uv-managed deps, ruff + mypy + pytest config
├── Dockerfile              # multi-stage build, runs on Railway
├── verbio_engine/
│   ├── main.py             # FastAPI app + /health + /ready
│   ├── config.py           # Pydantic Settings (boot-time validation)
│   ├── logging.py          # structlog (JSON in prod, console in dev)
│   ├── domain/             # canonical Pydantic shapes (brief §5)
│   └── schema_export.py    # CLI: dump JSON Schema → repo-root schemas/
└── tests/                  # pytest + hypothesis
```

Future phases add `state/`, `rules/`, `tick_loop.py`, `mouth/`, `tts/`, `persistence/`,
`commands/`, `agent.py`. The skeleton here is the smallest thing that satisfies Phase 0:
`/health` responds, domain types are exported, every quality gate runs.

## Prerequisites

- Python 3.12 (`uv python install 3.12`)
- [`uv`](https://docs.astral.sh/uv/) ≥ 0.7
- Docker Desktop (for the local Postgres + Redis stack at `infra/docker-compose.dev.yml`)

## Install

```bash
cd services/engine
uv sync --all-extras
```

`uv sync --frozen` is what CI runs; local dev should usually keep the lock and the manifest
in step.

## Common commands

| Task                            | Command                                                |
| ------------------------------- | ------------------------------------------------------ |
| Run dev server (hot reload)     | `uv run verbio-engine`                                 |
| Run dev server (explicit)       | `uv run uvicorn verbio_engine.main:app --reload`       |
| Lint                            | `uv run ruff check .`                                  |
| Format                          | `uv run ruff format .`                                 |
| Format (check only)             | `uv run ruff format --check .`                         |
| Typecheck (strict)              | `uv run mypy --strict verbio_engine`                   |
| Tests                           | `uv run pytest`                                        |
| Tests (with coverage)           | `uv run pytest --cov=verbio_engine --cov-report=term-missing` |
| Single test                     | `uv run pytest tests/domain/test_models.py::test_participant_state_minimal_construction` |
| Export JSON Schemas             | `uv run verbio-export-schemas`                         |
| Check JSON Schemas are in sync  | `uv run verbio-export-schemas --check`                 |
| Apply DB migrations             | `DATABASE_URL_DIRECT=… uv run alembic upgrade head`    |
| Show current revision           | `DATABASE_URL_DIRECT=… uv run alembic current`         |
| Build container                 | `docker build -t verbio-engine .`                      |

## How shared types stay in sync

`schemas/*.generated.json` at the repo root is the contract between this service and the web
app. The flow:

1. Pydantic models in `verbio_engine/domain/` are the source of truth.
2. `uv run verbio-export-schemas` writes one `*.generated.json` per top-level model to
   `../../schemas/`.
3. `packages/shared-types` reads those files and emits TypeScript via
   `json-schema-to-typescript`.
4. CI re-runs the whole pipeline on every PR and fails if the generated TS would change —
   that's the only way the web service learns about a domain change.

If you add or modify a model in `verbio_engine/domain/`, run `pnpm shared-types:generate`
at the repo root before opening a PR.

## Quality gates (brief §13)

- `ruff check .` — clean (no `noqa` without a justification).
- `ruff format --check .` — clean.
- `mypy --strict verbio_engine` — clean. Pydantic plugin enabled.
- `pytest` — green. Coverage gate of **85% on `verbio_engine/rules/` and
  `verbio_engine/tick_loop.py`** kicks in once those modules land (Phase 2+); Phase 0
  doesn't ship them yet.
