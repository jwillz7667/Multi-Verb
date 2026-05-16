/**
 * @verbio/shared-types — canonical domain types shared between verbio-web
 * and verbio-engine.
 *
 * These types are generated from the Pydantic models in
 * `services/engine/verbio_engine/domain/`. Do not edit `src/generated/` by
 * hand — the CI gate at `.github/workflows/ci.yml` (job: `shared-types`)
 * fails if the committed TypeScript drifts from the live schemas.
 *
 * Regenerate locally with: `pnpm shared-types:generate`.
 */

export type * from './generated/index.js';
