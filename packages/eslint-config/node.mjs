/**
 * @verbio/eslint-config/node — Node.js services overlay.
 *
 * Composes with ./base. Adds Node global definitions and disables
 * browser-oriented rules for server-only TypeScript packages.
 *
 * Usage:
 *
 *   import base from '@verbio/eslint-config/base';
 *   import node from '@verbio/eslint-config/node';
 *   export default [...base, ...node];
 */

import globals from 'globals';

export default [
  {
    files: ['**/*.{ts,mts,cts,js,mjs,cjs}'],
    languageOptions: {
      globals: {
        ...globals.node,
      },
    },
    rules: {
      'no-console': 'off',
    },
  },
];
