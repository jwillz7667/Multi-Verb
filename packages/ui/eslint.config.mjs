import base from '@verbio/eslint-config/base';
import next from '@verbio/eslint-config/next';

export default [
  ...base,
  ...next,
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
