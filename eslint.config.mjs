// Verbio root ESLint flat config — https://eslint.org/docs/latest/use/configure/configuration-files
//
// Workspace packages (apps/web, packages/*) each have their own
// eslint.config.mjs. This root config is what runs when eslint is
// invoked from the repo root (e.g., via lefthook on staged files
// scattered across packages). It delegates to @verbio/eslint-config
// so the rule set stays single-sourced.

import globals from 'globals';

import base from '@verbio/eslint-config/base';

export default [
  {
    ignores: [
      'packages/shared-types/src/generated/**',
      // Next.js writes this file; the triple-slash directives are required.
      '**/next-env.d.ts',
      // Build/test artifacts not already covered by base.
      '**/.next/**',
      '**/coverage/**',
    ],
  },
  ...base,
  {
    // Plain JS configs and build scripts at the repo root need Node globals.
    files: ['**/*.{js,mjs,cjs}'],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
];
