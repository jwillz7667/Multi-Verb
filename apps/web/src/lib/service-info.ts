/**
 * Build-time constants surfaced by the /api/health and /api/ready
 * endpoints. Bumped here on release; package.json is the canonical
 * version source but reading it from a Next.js route at runtime is
 * fragile (path differs between Vercel build output and dev), so we
 * mirror it as a constant maintained by the release script.
 */

export const SERVICE_NAME = 'verbio-web';
export const SERVICE_VERSION = '0.0.0';
