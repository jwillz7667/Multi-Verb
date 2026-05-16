/**
 * @verbio/eslint-config/next — Next.js + React + a11y overlay.
 *
 * Composes with ./base. Apps using this preset get Next.js routing
 * conventions, React hooks rules, and JSX accessibility checks on top
 * of the base TS rules.
 *
 * Usage:
 *
 *   // apps/web/eslint.config.mjs
 *   import base from '@verbio/eslint-config/base';
 *   import next from '@verbio/eslint-config/next';
 *   export default [...base, ...next];
 */

import nextPlugin from '@next/eslint-plugin-next';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';

export default [
  {
    files: ['**/*.{ts,tsx,js,jsx}'],
    plugins: {
      '@next/next': nextPlugin,
      react,
      'react-hooks': reactHooks,
      'jsx-a11y': jsxA11y,
    },
    languageOptions: {
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
      globals: {
        React: 'readonly',
      },
    },
    settings: {
      react: { version: 'detect' },
    },
    rules: {
      ...nextPlugin.configs.recommended.rules,
      ...nextPlugin.configs['core-web-vitals'].rules,

      ...react.configs.flat.recommended.rules,
      ...react.configs.flat['jsx-runtime'].rules,

      ...reactHooks.configs.recommended.rules,

      ...jsxA11y.flatConfigs.recommended.rules,

      'react/prop-types': 'off',
      'react/react-in-jsx-scope': 'off',
      'react/jsx-uses-react': 'off',
      'react/self-closing-comp': 'error',
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
    },
  },
];
