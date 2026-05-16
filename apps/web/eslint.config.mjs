import base from '@verbio/eslint-config/base';
import nextPreset from '@verbio/eslint-config/next';

export default [
  {
    ignores: ['.next/**', 'next-env.d.ts', 'public/**', 'dist/**', 'coverage/**'],
  },
  ...base,
  ...nextPreset,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
];
