// Commitlint configuration — Conventional Commits, enforced in CI + pre-commit-msg hook.
// https://commitlint.js.org/

/** @type {import('@commitlint/types').UserConfig} */
export default {
  extends: ['@commitlint/config-conventional'],
  rules: {
    'body-leading-blank': [2, 'always'],
    'footer-leading-blank': [2, 'always'],
    'header-max-length': [2, 'always', 100],
    'subject-case': [0],
    'subject-empty': [2, 'never'],
    'subject-full-stop': [2, 'never', '.'],
    'type-case': [2, 'always', 'lower-case'],
    'type-empty': [2, 'never'],
    'type-enum': [
      2,
      'always',
      [
        'feat',
        'fix',
        'refactor',
        'perf',
        'test',
        'docs',
        'chore',
        'build',
        'ci',
        'revert',
        'style',
      ],
    ],
    'scope-enum': [
      2,
      'always',
      [
        // Empty allowed.
        '',
        // Apps & services
        'web',
        'engine',
        // Packages
        'shared-types',
        'ui',
        'eslint-config',
        // Cross-cutting
        'schemas',
        'infra',
        'db',
        'auth',
        'r2',
        'railway',
        'vercel',
        'docs',
        'ci',
        'deps',
        // Domain areas (engine)
        'rules',
        'tick-loop',
        'state',
        'mouth',
        'tts',
        'commands',
        'persistence',
        'agent',
        // Domain areas (web)
        'dashboard',
        'replay',
        'sse',
      ],
    ],
  },
};
