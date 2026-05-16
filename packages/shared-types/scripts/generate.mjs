#!/usr/bin/env node
/**
 * Regenerate TypeScript types from the engine's Pydantic models.
 *
 * Flow:
 *   1. Invoke `uv run verbio-export-schemas` in services/engine to refresh
 *      `schemas/*.generated.json` at the repo root.
 *   2. For each schema, compile it to TypeScript with json-schema-to-typescript.
 *   3. Write one `src/generated/<kebab-name>.ts` per schema, plus a barrel
 *      `src/generated/index.ts` that re-exports everything.
 *
 * Determinism is essential — CI fails on any drift after this script runs.
 * Same Pydantic input → same TypeScript output every time.
 */

import { execSync } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { compile } from 'json-schema-to-typescript';

const here = dirname(fileURLToPath(import.meta.url));
const packageRoot = resolve(here, '..');
const repoRoot = resolve(packageRoot, '..', '..');
const schemasDir = join(repoRoot, 'schemas');
const engineDir = join(repoRoot, 'services', 'engine');
const outDir = join(packageRoot, 'src', 'generated');

const log = (msg) => process.stdout.write(`[shared-types] ${msg}\n`);
const fail = (msg) => {
  process.stderr.write(`[shared-types] ${msg}\n`);
  process.exit(1);
};

log('refreshing JSON schemas from Pydantic models...');
try {
  execSync(`uv run --project "${engineDir}" verbio-export-schemas --out-dir "${schemasDir}"`, {
    stdio: 'inherit',
  });
} catch (err) {
  fail(`failed to run verbio-export-schemas — is uv installed and the engine project synced? (${err.message})`);
}

if (!existsSync(schemasDir)) {
  fail(`no schemas directory at ${schemasDir}`);
}

const schemaFiles = readdirSync(schemasDir)
  .filter((f) => f.endsWith('.generated.json'))
  .sort();

if (schemaFiles.length === 0) {
  fail(`no .generated.json files in ${schemasDir}`);
}

mkdirSync(outDir, { recursive: true });

const bannerComment = [
  '/**',
  ' * AUTO-GENERATED — DO NOT EDIT BY HAND.',
  ' *',
  ' * Source: Pydantic models in services/engine/verbio_engine/domain/.',
  ' * Regenerate via: pnpm shared-types:generate',
  ' */',
  '',
  '/* eslint-disable */',
].join('\n');

const compileOptions = {
  bannerComment,
  declareExternallyReferenced: true,
  enableConstEnums: false,
  format: true,
  unknownAny: true,
  strictIndexSignatures: true,
  additionalProperties: false,
  style: {
    bracketSpacing: true,
    printWidth: 100,
    semi: true,
    singleQuote: true,
    tabWidth: 2,
    trailingComma: 'all',
  },
};

const indexExports = [];

for (const file of schemaFiles) {
  const stem = file.replace(/\.generated\.json$/u, '');
  const kebab = stem.replace(/_/gu, '-');
  const schemaPath = join(schemasDir, file);
  const schemaText = readFileSync(schemaPath, 'utf-8');
  const schema = JSON.parse(schemaText);

  const ts = await compile(schema, schema.title, compileOptions);
  const outPath = join(outDir, `${kebab}.ts`);
  writeFileSync(outPath, ts, 'utf-8');
  indexExports.push(`export type * from './${kebab}.js';`);
  log(`wrote ${relative(repoRoot, outPath)}`);
}

const indexContents =
  [
    '/**',
    ' * AUTO-GENERATED barrel — re-exports every generated domain type.',
    ' * See sibling files (one per Pydantic model).',
    ' */',
    '',
    ...indexExports,
    '',
  ].join('\n');

writeFileSync(join(outDir, 'index.ts'), indexContents, 'utf-8');
log(`wrote ${relative(repoRoot, join(outDir, 'index.ts'))}`);
log('done.');
