# @verbio/eslint-config

Shared ESLint flat-config presets for the Verbio monorepo.

## Presets

| Subpath | Purpose |
| --- | --- |
| `@verbio/eslint-config/base` | Universal TS rules: `typescript-eslint` strict + stylistic type-checked, import ordering, no-floating-promises, no enums. |
| `@verbio/eslint-config/next` | Overlay for `apps/web`: Next.js 15 core-web-vitals, React, react-hooks, jsx-a11y. |
| `@verbio/eslint-config/node` | Overlay for Node-only TS packages: Node globals, relaxed `no-console`. |

## Usage

```js
// apps/web/eslint.config.mjs
import base from '@verbio/eslint-config/base';
import next from '@verbio/eslint-config/next';

export default [...base, ...next];
```

```js
// packages/some-node-package/eslint.config.mjs
import base from '@verbio/eslint-config/base';
import node from '@verbio/eslint-config/node';

export default [...base, ...node];
```

## Why a package, not inline configs?

Three reasons:

1. **One source of truth for the rule set.** A new rule lands in `base.mjs`
   and every consumer gets it on the next install. Drift between
   `apps/web/eslint.config.mjs` and a hypothetical `apps/admin/...`
   becomes impossible.
2. **Bring-your-own dependencies stay scoped.** Next.js / React / a11y
   plugins live in `peerDependencies` of consumers and `dependencies`
   of this package; they don't pollute the root `package.json`.
3. **Flat-config composition is just array spread.** Consumers can mix
   `base` + `next`, or `base` + `node`, or add their own overlay file
   on top — no config-extends magic.

## Why ban enums?

Per repo convention (see `no-restricted-syntax` in `base.mjs`),
TypeScript `enum` declarations are banned in favor of union types or
`as const` objects. Enums emit runtime objects and have surprising
behavior under `--isolatedModules`; unions are erased at compile time
and play nicely with the Pydantic-generated `Literal` types we get
from `@verbio/shared-types`.
