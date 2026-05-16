# @verbio/shared-types

Generated TypeScript types for the domain shapes defined in
`services/engine/verbio_engine/domain/`. Consumed by `apps/web` and any other
TypeScript package that needs to talk in the same language as the engine.

**Never edit `src/generated/` by hand.** The CI workflow `shared-types · generated TS up to date`
fails if the committed files drift from the live Pydantic models.

## The pipeline

```
services/engine/verbio_engine/domain/*.py   (source of truth)
  │
  │  uv run verbio-export-schemas
  ▼
schemas/*.generated.json                    (JSON Schema, committed)
  │
  │  pnpm shared-types:generate
  ▼
packages/shared-types/src/generated/*.ts    (TypeScript, committed)
  │
  ▼
apps/web → import type { ParticipantState } from '@verbio/shared-types';
```

The schemas are committed so the web service can build without a working
Python toolchain locally. The TypeScript is committed so CI can `git diff`
to detect drift.

## Regenerate

```bash
pnpm shared-types:generate
```

Re-runs the engine's `verbio-export-schemas` CLI, then compiles each schema
through `json-schema-to-typescript`. The output is byte-for-byte
deterministic; if the diff is empty, your changes did not affect the
contract.

## Adding a new domain type

1. Add or modify the Pydantic model in
   `services/engine/verbio_engine/domain/`.
2. Register the model in `services/engine/verbio_engine/schema_export.py`
   (`EXPORTED_MODELS` tuple).
3. Add a test asserting the contract in
   `services/engine/tests/domain/test_models.py`.
4. From the repo root, run `pnpm shared-types:generate`.
5. Commit the changes to `services/engine/verbio_engine/domain/`,
   `schemas/`, and `packages/shared-types/src/generated/` together — they
   are one logical change.

## Why generate, not hand-write?

The brief (§5) is explicit: the domain shapes are defined once, in
Pydantic, and everything else is generated from them. The reason: any
divergence between what the engine writes to Postgres and what the web app
reads is a P0 audit-trail bug. Generation makes drift impossible at build
time.

## What lives here

- `package.json` — exports `./src/index.ts` as the public surface.
- `scripts/generate.mjs` — the generator (Node + `json-schema-to-typescript`).
- `src/index.ts` — public barrel; re-exports everything from `src/generated/`.
- `src/generated/` — auto-generated TypeScript; committed; never edited by hand.
- `tsconfig.json` — typecheck-only (no emit); consumers transpile the source.
