import base from '@verbio/eslint-config/base';
import nextPreset from '@verbio/eslint-config/next';

/**
 * Selector banning direct `db.<tenantModel>` access — see ADR-0002 /
 * brief §10.3. Every read of a tenant-owned model must flow through
 * `scopedDb(orgId)` (apps/web/src/lib/scoped-db.ts) so the orgId
 * filter is impossible to forget at the call site. The selector
 * matches `MemberExpression` where the object is the identifier `db`
 * and the property is one of the tenant-owned model names from
 * prisma/schema.prisma. New tenant models added to the schema must
 * be added to this regex.
 */
const TENANT_MODELS_RE =
  '^(study|moderatedSession|participant|utterance|stateSnapshot|decision|ruleEvaluation|researcherAction|sessionFlag)$';

const noDirectDbAccess = {
  selector: `MemberExpression[object.name="db"][property.name=/${TENANT_MODELS_RE}/]`,
  message:
    'Tenant-owned models must go through `scopedDb(orgId)` (apps/web/src/lib/scoped-db.ts). See ADR-0002 / brief §10.3. The few legitimate exceptions (admin cron, LiveKit webhook) use `unscopedDbForAdminCron(reason)` from the same module.',
};

/**
 * Files that still call `db.<tenantModel>` directly. Each is on a
 * documented migration path:
 *
 *   - features/sessions/repo.ts, features/studies/repo.ts,
 *     app/room/[roomName]/page.tsx — migrated to scopedDb in P7 L2.
 *
 * Adding files here should be a deliberate review decision, not a
 * convenience escape hatch. New code should use `scopedDb(orgId)` or
 * the explicit `unscopedDbForAdminCron(reason)` helper.
 */
const TENANT_GUARD_ALLOWLIST = [
  'src/features/sessions/repo.ts',
  'src/features/studies/repo.ts',
  // The `[roomName]` segment is a glob character class in minimatch, so
  // we match by directory depth instead of literal name. There is only
  // one `room/*/page.tsx` in the App Router today.
  'src/app/room/*/page.tsx',
];

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
  {
    files: ['src/**/*.{ts,tsx}'],
    rules: {
      // Re-state the parent TSEnumDeclaration ban — `no-restricted-syntax`
      // is an array rule but ESLint does not merge entries across configs,
      // so the last value wins outright. Keeping both selectors here is
      // the only way to enforce both guards on the web app.
      'no-restricted-syntax': [
        'error',
        {
          selector: 'TSEnumDeclaration',
          message: 'Use union types or `as const` objects instead of enums.',
        },
        noDirectDbAccess,
      ],
    },
  },
  {
    files: TENANT_GUARD_ALLOWLIST,
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector: 'TSEnumDeclaration',
          message: 'Use union types or `as const` objects instead of enums.',
        },
      ],
    },
  },
];
