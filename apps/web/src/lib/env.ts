/**
 * Validated runtime environment for verbio-web.
 *
 * Boot-time validation: this module reads `process.env`, validates it
 * with Zod, and exports the typed result. Importing `env` from any
 * server or client module triggers validation; missing or malformed
 * vars throw a `ZodError` with the field path so the failure is loud
 * and immediate rather than surfacing as a `undefined.split is not a
 * function` deep in a request handler.
 *
 * Two schemas, intentionally separated:
 *   - `serverEnv` — full secret-bearing env, only safe in server code.
 *   - `clientEnv` — `NEXT_PUBLIC_*` only, safe to ship to the browser.
 *
 * `SKIP_ENV_VALIDATION=1` bypasses validation entirely. Set it only
 * for `next build` in CI / Docker builds, where Next imports every
 * route handler to collect page data and would otherwise fail on
 * missing production secrets. Never set it for runtime processes.
 */

import { z } from 'zod';

// `.env` placeholder files commonly set optional vars to an empty
// string. Treat empty strings as "absent" so `.optional()` actually
// works the way you'd expect — otherwise a placeholder line like
// `AUTH_RESEND_KEY=` would fail `min(1)` validation at boot.
const emptyAsUndefined = z.preprocess(
  (v) => (typeof v === 'string' && v.trim() === '' ? undefined : v),
  z.unknown(),
);
const optionalSecret = emptyAsUndefined.pipe(z.string().min(1).optional());
const requiredSecret = (label: string) =>
  emptyAsUndefined.pipe(
    z.string({ required_error: `${label} is required` }).min(1, `${label} must not be empty`),
  );

const url = (label: string) =>
  emptyAsUndefined.pipe(
    z.string({ required_error: `${label} is required` }).url(`${label} must be a valid URL`),
  );

const optionalUrl = emptyAsUndefined.pipe(z.string().url().optional());

const clientSchema = z.object({
  NEXT_PUBLIC_APP_URL: url('NEXT_PUBLIC_APP_URL'),
  NEXT_PUBLIC_APP_NAME: z.string().min(1).default('Verbio'),
  NEXT_PUBLIC_LIVEKIT_URL: optionalUrl,
});

const serverSchema = clientSchema.extend({
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),

  DATABASE_URL_POOLED: url('DATABASE_URL_POOLED'),
  DATABASE_URL_DIRECT: optionalUrl,

  REDIS_URL: url('REDIS_URL'),
  REDIS_NAMESPACE: z.string().min(1).default('verbio:local'),

  AUTH_SECRET: requiredSecret('AUTH_SECRET'),
  AUTH_RESEND_KEY: optionalSecret,
  // Resend accepts either a bare email (`noreply@x.com`) or RFC 5322
  // name format (`Verbio <noreply@x.com>`). Validate either is present.
  AUTH_EMAIL_FROM: emptyAsUndefined.pipe(z.string().min(3).optional()),

  // `dev:auto-sign-in` — bypasses Auth.js in local dev; see auth.config.ts
  DEV_AUTO_SIGN_IN_USER: optionalSecret,
  DEV_AUTO_SIGN_IN_EMAIL: optionalSecret,

  R2_ACCOUNT_ID: optionalSecret,
  R2_ACCESS_KEY_ID: optionalSecret,
  R2_SECRET_ACCESS_KEY: optionalSecret,
  R2_BUCKET: z.string().min(1).optional(),
  R2_PUBLIC_BASE_URL: optionalUrl,

  ENGINE_BASE_URL: url('ENGINE_BASE_URL'),
  ENGINE_ADMIN_TOKEN: requiredSecret('ENGINE_ADMIN_TOKEN'),

  LIVEKIT_API_KEY: optionalSecret,
  LIVEKIT_API_SECRET: optionalSecret,
  // Server-side URL — wss:// for browsers, can also accept https://
  // for Agent dispatch REST calls (the SDK normalises internally).
  LIVEKIT_URL: optionalUrl,
});

export type ClientEnv = z.infer<typeof clientSchema>;
export type ServerEnv = z.infer<typeof serverSchema>;

interface ParseResult<T> {
  data: T;
  isValid: true;
}

interface ParseFailure {
  error: z.ZodError;
  isValid: false;
}

export function parseServerEnv(
  input: Record<string, string | undefined>,
): ParseResult<ServerEnv> | ParseFailure {
  const result = serverSchema.safeParse(input);
  if (result.success) {
    return { data: result.data, isValid: true };
  }
  return { error: result.error, isValid: false };
}

export function parseClientEnv(
  input: Record<string, string | undefined>,
): ParseResult<ClientEnv> | ParseFailure {
  const projected: Record<string, string | undefined> = {
    NEXT_PUBLIC_APP_URL: input['NEXT_PUBLIC_APP_URL'],
    NEXT_PUBLIC_APP_NAME: input['NEXT_PUBLIC_APP_NAME'],
    NEXT_PUBLIC_LIVEKIT_URL: input['NEXT_PUBLIC_LIVEKIT_URL'],
  };
  const result = clientSchema.safeParse(projected);
  if (result.success) {
    return { data: result.data, isValid: true };
  }
  return { error: result.error, isValid: false };
}

function formatZodError(err: z.ZodError): string {
  const lines = err.issues.map((issue) => {
    const path = issue.path.join('.') || '(root)';
    return `  - ${path}: ${issue.message}`;
  });
  return ['Invalid environment configuration for verbio-web:', ...lines].join('\n');
}

function readServerEnv(): ServerEnv {
  const parsed = parseServerEnv(process.env);
  if (!parsed.isValid) {
    throw new Error(formatZodError(parsed.error));
  }
  return parsed.data;
}

function readClientEnv(): ClientEnv {
  const parsed = parseClientEnv(process.env);
  if (!parsed.isValid) {
    throw new Error(formatZodError(parsed.error));
  }
  return parsed.data;
}

const isServer = typeof window === 'undefined';
const skipValidation = process.env['SKIP_ENV_VALIDATION'] === '1';

function buildEnv(): ServerEnv | ClientEnv {
  if (skipValidation) {
    return process.env as unknown as ServerEnv;
  }
  return isServer ? readServerEnv() : readClientEnv();
}

export const env: ServerEnv | ClientEnv = buildEnv();

export const serverEnv: ServerEnv | null = isServer ? (env as ServerEnv) : null;
export const clientEnv: ClientEnv = env;
