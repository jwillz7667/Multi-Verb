/**
 * Structured logger for verbio-web (P7 L4).
 *
 * One log surface, two output modes:
 *   - JSON lines in production. Each line is a single object with
 *     `ts`, `level`, `service`, `event`, plus the call-site fields.
 *     Vercel + Railway ingest these natively, and Sentry parses the
 *     same JSON when an event includes an attached exception.
 *   - Pretty key=value in development. ANSI colours when stdout is a
 *     TTY (matches the engine's `structlog.dev.ConsoleRenderer`).
 *
 * Bound loggers (`logger.child({ ... })`) carry their parent's fields
 * forward — use this at request entry to pin `sessionId`, `orgId`,
 * `userId` so every downstream call inherits them without re-passing.
 *
 * Sentry: when `SENTRY_DSN` is configured, `logger.error(...)` (and
 * `logger.warn(...)` for level=warn) forward to Sentry via the lazily
 * imported `@sentry/nextjs` SDK. The import is dynamic so this module
 * stays usable on the edge runtime / in tests / when Sentry is absent
 * (server-side guard in `instrumentation.ts` covers init).
 *
 * Eslint: this module is the ONE place `console.log` / `console.info`
 * are allowed (see the file-level override in `apps/web/eslint.config.mjs`).
 */

import 'server-only';

// Read NODE_ENV directly (rather than going through `./env`) so the
// logger stays usable BEFORE env validation runs — boot-time env errors
// need to be loggable too, and a circular `env imports logger imports
// env` would crash the process before any log line emits.
function isProductionEnv(): boolean {
  return process.env.NODE_ENV === 'production';
}

export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

export type LogFields = Record<string, unknown>;

export interface Logger {
  debug(event: string, fields?: LogFields): void;
  info(event: string, fields?: LogFields): void;
  warn(event: string, fields?: LogFields): void;
  /**
   * Logs at error level. If `errOrFields` is an `Error`, the message
   * and stack are extracted into the structured payload AND forwarded
   * to Sentry (when configured); any additional fields go in `fields`.
   * Passing a plain field bag is fine for "expected" error paths that
   * shouldn't page on-call.
   */
  error(event: string, errOrFields?: Error | LogFields, fields?: LogFields): void;
  child(bindings: LogFields): Logger;
}

const LEVEL_ORDER: Record<LogLevel, number> = {
  debug: 10,
  info: 20,
  warn: 30,
  error: 40,
};

function resolveMinLevel(): number {
  const raw = process.env['LOG_LEVEL']?.toLowerCase();
  if (raw === 'debug' || raw === 'info' || raw === 'warn' || raw === 'error') {
    return LEVEL_ORDER[raw];
  }
  // Default: info in production, debug in dev/test so local iteration
  // sees everything without flipping an env var.
  return isProductionEnv() ? LEVEL_ORDER.info : LEVEL_ORDER.debug;
}

// Memoised so test runners that flip NODE_ENV at runtime keep the
// behaviour they had at first import — matches how the env.ts singleton
// works and avoids surprising mid-process level changes.
let minLevelCache: number | null = null;
function minLevel(): number {
  minLevelCache ??= resolveMinLevel();
  return minLevelCache;
}

/** Test-only reset. Not exported from the barrel. */
export function __resetLoggerForTests(): void {
  minLevelCache = null;
}

interface LogRecord {
  ts: string;
  level: LogLevel;
  service: string;
  event: string;
  err?: { name: string; message: string; stack?: string };
  [key: string]: unknown;
}

function buildRecord(
  level: LogLevel,
  event: string,
  bindings: LogFields,
  fields: LogFields | undefined,
  err: Error | null,
): LogRecord {
  const record: LogRecord = {
    ts: new Date().toISOString(),
    level,
    service: 'verbio-web',
    event,
    ...bindings,
    ...(fields ?? {}),
  };
  if (err !== null) {
    const serialised: { name: string; message: string; stack?: string } = {
      name: err.name,
      message: err.message,
    };
    if (err.stack !== undefined) serialised.stack = err.stack;
    record.err = serialised;
  }
  return record;
}

// Pretty renderer — single line, key=value pairs after the message.
// Mirrors structlog.dev.ConsoleRenderer's layout closely enough that a
// dev who's read both knows what they're looking at.
function renderPretty(record: LogRecord): string {
  const { ts, level, event, service, err, ...rest } = record;
  const parts: string[] = [ts, `[${level.toUpperCase()}]`, `${service}.${event}`];
  for (const [key, value] of Object.entries(rest)) {
    parts.push(`${key}=${JSON.stringify(value)}`);
  }
  if (err) {
    parts.push(`err=${err.name}:${err.message}`);
  }
  return parts.join(' ');
}

// `JSON.stringify` rejects BigInt and silently drops circular refs.
// Bigints land as `"42n"` strings (rare in our domain — only Prisma
// returns them for `BigInt` columns like tick_id). Circulars are
// replaced with `"[Circular]"` so a buggy log call never crashes the
// request.
function safeStringify(value: unknown): string {
  const seen = new WeakSet<object>();
  return JSON.stringify(value, (_key, v: unknown) => {
    if (typeof v === 'bigint') return `${v.toString()}n`;
    if (typeof v === 'object' && v !== null) {
      if (seen.has(v)) return '[Circular]';
      seen.add(v);
    }
    return v;
  });
}

function emit(record: LogRecord): void {
  const line = isProductionEnv() ? safeStringify(record) : renderPretty(record);
  if (record.level === 'error') {
    console.error(line);
  } else if (record.level === 'warn') {
    console.warn(line);
  } else {
    // eslint-disable-next-line no-console -- centralised logger sink for info/debug
    console.log(line);
  }
}

// Sentry forwarding. The `@sentry/nextjs` module attaches to a global
// hub on init (see `instrumentation.ts`); `captureException` /
// `captureMessage` are cheap no-ops when no DSN is configured, but
// the static import would bloat edge bundles and complicate testing.
// Lazy dynamic import keeps the logger import-free.
interface SentryLike {
  captureException: (err: unknown, hint?: { extra?: LogFields; tags?: LogFields }) => void;
  captureMessage: (
    msg: string,
    options?: { level: 'warning' | 'error'; extra?: LogFields },
  ) => void;
}
let cachedSentry: SentryLike | null | undefined;
async function getSentry(): Promise<SentryLike | null> {
  if (cachedSentry !== undefined) return cachedSentry;
  if (!process.env['SENTRY_DSN']) {
    cachedSentry = null;
    return null;
  }
  try {
    const mod: unknown = await import('@sentry/nextjs');
    cachedSentry = mod as SentryLike;
    return cachedSentry;
  } catch {
    // Sentry not installed — treat as disabled. Mirrors the engine
    // side, which also no-ops when `sentry-sdk` is absent.
    cachedSentry = null;
    return null;
  }
}

function forwardToSentry(record: LogRecord, err: Error | null): void {
  if (record.level !== 'error' && record.level !== 'warn') return;
  // Fire-and-forget — Sentry's transport queues internally. The
  // logger contract is synchronous and we don't want callers to await
  // a network round-trip just to log an error.
  void getSentry().then((sentry) => {
    if (sentry === null) return;
    if (err !== null) {
      // Spread the record first so the explicit `event` key takes
      // precedence — `record.event` is already in the spread, but
      // making the intent obvious documents that Sentry's
      // `extra.event` is the authoritative field name searchers will
      // filter on.
      sentry.captureException(err, { extra: { ...record, event: record.event } });
    } else {
      sentry.captureMessage(record.event, {
        level: record.level === 'warn' ? 'warning' : 'error',
        extra: record,
      });
    }
  });
}

function createLogger(bindings: LogFields): Logger {
  function log(level: LogLevel, event: string, fields?: LogFields, err: Error | null = null): void {
    if (LEVEL_ORDER[level] < minLevel()) return;
    const record = buildRecord(level, event, bindings, fields, err);
    emit(record);
    forwardToSentry(record, err);
  }

  return {
    debug(event, fields) {
      log('debug', event, fields);
    },
    info(event, fields) {
      log('info', event, fields);
    },
    warn(event, fields) {
      log('warn', event, fields);
    },
    error(event, errOrFields, fields) {
      if (errOrFields instanceof Error) {
        log('error', event, fields, errOrFields);
      } else {
        log('error', event, errOrFields);
      }
    },
    child(extraBindings) {
      return createLogger({ ...bindings, ...extraBindings });
    },
  };
}

/** Root application logger. Use `.child({ component: '...' })` per module. */
export const logger: Logger = createLogger({});
