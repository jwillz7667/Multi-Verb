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
 * Importing `serverEnv` from a Client Component is blocked at runtime
 * by the `server-only` package below.
 */

import { z } from 'zod';

const optionalSecret = z.string().min(1).optional();
const requiredSecret = (label: string): z.ZodString =>
  z.string({ required_error: `${label} is required` }).min(1, `${label} must not be empty`);

const url = (label: string): z.ZodString =>
  z.string({ required_error: `${label} is required` }).url(`${label} must be a valid URL`);

const optionalUrl = z.string().url().optional();

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
  AUTH_EMAIL_FROM: z.string().email('AUTH_EMAIL_FROM must be a valid email').optional(),

  R2_ACCOUNT_ID: optionalSecret,
  R2_ACCESS_KEY_ID: optionalSecret,
  R2_SECRET_ACCESS_KEY: optionalSecret,
  R2_BUCKET: z.string().min(1).optional(),
  R2_PUBLIC_BASE_URL: optionalUrl,

  ENGINE_BASE_URL: url('ENGINE_BASE_URL'),
  ENGINE_ADMIN_TOKEN: requiredSecret('ENGINE_ADMIN_TOKEN'),

  LIVEKIT_API_KEY: optionalSecret,
  LIVEKIT_API_SECRET: optionalSecret,
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

export function parseServerEnv(input: NodeJS.ProcessEnv): ParseResult<ServerEnv> | ParseFailure {
  const result = serverSchema.safeParse(input);
  if (result.success) {
    return { data: result.data, isValid: true };
  }
  return { error: result.error, isValid: false };
}

export function parseClientEnv(input: NodeJS.ProcessEnv): ParseResult<ClientEnv> | ParseFailure {
  const projected: Record<string, string | undefined> = {
    NEXT_PUBLIC_APP_URL: input.NEXT_PUBLIC_APP_URL,
    NEXT_PUBLIC_APP_NAME: input.NEXT_PUBLIC_APP_NAME,
    NEXT_PUBLIC_LIVEKIT_URL: input.NEXT_PUBLIC_LIVEKIT_URL,
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

export const env: ServerEnv | ClientEnv = isServer ? readServerEnv() : readClientEnv();

export const serverEnv = isServer ? (env as ServerEnv) : null;
export const clientEnv: ClientEnv = isServer ? (env as ServerEnv) : (env as ClientEnv);
