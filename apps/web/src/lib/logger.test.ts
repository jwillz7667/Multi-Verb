/**
 * Logger contract tests (P7 L4).
 *
 * Asserts the structural shape callers depend on:
 *   - Levels filter correctly (`LOG_LEVEL` env honored, defaults sane)
 *   - JSON mode emits one parseable line per call (production)
 *   - Pretty mode is human-readable (dev/test)
 *   - `child(bindings)` carries fields forward
 *   - `error(event, err)` extracts name/message/stack
 *   - Safe stringify handles BigInt + circular refs without throwing
 *
 * Sentry forwarding is asserted via a `vi.mock('@sentry/nextjs')` shim
 * that captures the calls; the real DSN-gated wiring is integration
 * territory and lives in instrumentation.ts.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const captureExceptionMock = vi.fn();
const captureMessageMock = vi.fn();

vi.mock('@sentry/nextjs', () => ({
  captureException: captureExceptionMock,
  captureMessage: captureMessageMock,
}));

async function freshLogger() {
  vi.resetModules();
  const mod = await import('./logger');
  mod.__resetLoggerForTests();
  return mod;
}

// `process.env` in `@types/node` types NODE_ENV as readonly. Tests
// genuinely need to flip it; route assignments through a helper that
// punches a tiny hole in the type rather than sprinkling `as any` or
// individual eslint-disables across every assignment.
function setEnv(name: string, value: string | undefined): void {
  const env = process.env as Record<string, string | undefined>;
  if (value === undefined) {
    // eslint-disable-next-line @typescript-eslint/no-dynamic-delete -- env name is a literal at every call site
    delete env[name];
  } else env[name] = value;
}

describe('logger', () => {
  let logSpy: ReturnType<typeof vi.spyOn>;
  let warnSpy: ReturnType<typeof vi.spyOn>;
  let errorSpy: ReturnType<typeof vi.spyOn>;
  let originalNodeEnv: string | undefined;
  let originalLogLevel: string | undefined;
  let originalSentryDsn: string | undefined;

  beforeEach(() => {
    const noop = (): void => undefined;
    logSpy = vi.spyOn(console, 'log').mockImplementation(noop);
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(noop);
    errorSpy = vi.spyOn(console, 'error').mockImplementation(noop);
    originalNodeEnv = process.env.NODE_ENV;
    originalLogLevel = process.env['LOG_LEVEL'];
    originalSentryDsn = process.env['SENTRY_DSN'];
    delete process.env['SENTRY_DSN'];
    captureExceptionMock.mockClear();
    captureMessageMock.mockClear();
  });

  afterEach(() => {
    logSpy.mockRestore();
    warnSpy.mockRestore();
    errorSpy.mockRestore();
    setEnv('NODE_ENV', originalNodeEnv);
    setEnv('LOG_LEVEL', originalLogLevel);
    setEnv('SENTRY_DSN', originalSentryDsn);
  });

  describe('level filtering', () => {
    it('defaults to info in production', async () => {
      setEnv('NODE_ENV', 'production');
      delete process.env['LOG_LEVEL'];

      const { logger } = await freshLogger();
      logger.debug('debug.msg');
      logger.info('info.msg');

      expect(logSpy).toHaveBeenCalledTimes(1);
      expect(logSpy.mock.calls[0]?.[0]).toContain('info.msg');
    });

    it('defaults to debug outside production', async () => {
      setEnv('NODE_ENV', 'development');
      delete process.env['LOG_LEVEL'];

      const { logger } = await freshLogger();
      logger.debug('debug.msg');

      expect(logSpy).toHaveBeenCalledTimes(1);
    });

    it('honors LOG_LEVEL=warn', async () => {
      setEnv('NODE_ENV', 'production');
      process.env['LOG_LEVEL'] = 'warn';

      const { logger } = await freshLogger();
      logger.info('skipped');
      logger.warn('emitted');
      logger.error('also.emitted');

      expect(logSpy).not.toHaveBeenCalled();
      expect(warnSpy).toHaveBeenCalledTimes(1);
      expect(errorSpy).toHaveBeenCalledTimes(1);
    });
  });

  describe('output format', () => {
    it('emits JSON in production', async () => {
      setEnv('NODE_ENV', 'production');

      const { logger } = await freshLogger();
      logger.info('event.name', { sessionId: 'abc', count: 5 });

      const line = logSpy.mock.calls[0]?.[0] as string;
      const parsed = JSON.parse(line) as Record<string, unknown>;
      expect(parsed['event']).toBe('event.name');
      expect(parsed['level']).toBe('info');
      expect(parsed['service']).toBe('verbio-web');
      expect(parsed['sessionId']).toBe('abc');
      expect(parsed['count']).toBe(5);
      expect(typeof parsed['ts']).toBe('string');
    });

    it('emits pretty key=value in development', async () => {
      setEnv('NODE_ENV', 'development');

      const { logger } = await freshLogger();
      logger.info('event.name', { sessionId: 'abc' });

      const line = logSpy.mock.calls[0]?.[0] as string;
      expect(line).toContain('[INFO]');
      expect(line).toContain('verbio-web.event.name');
      expect(line).toContain('sessionId="abc"');
    });

    it('routes by level: error→console.error, warn→console.warn, info/debug→console.log', async () => {
      setEnv('NODE_ENV', 'development');

      const { logger } = await freshLogger();
      logger.debug('a');
      logger.info('b');
      logger.warn('c');
      logger.error('d');

      expect(logSpy).toHaveBeenCalledTimes(2);
      expect(warnSpy).toHaveBeenCalledTimes(1);
      expect(errorSpy).toHaveBeenCalledTimes(1);
    });
  });

  describe('child loggers', () => {
    it('inherits parent bindings and adds its own', async () => {
      setEnv('NODE_ENV', 'production');

      const { logger } = await freshLogger();
      const child = logger.child({ sessionId: 'sess-1' });
      const grand = child.child({ requestId: 'req-1' });

      grand.info('nested');

      const line = logSpy.mock.calls[0]?.[0] as string;
      const parsed = JSON.parse(line) as Record<string, unknown>;
      expect(parsed['sessionId']).toBe('sess-1');
      expect(parsed['requestId']).toBe('req-1');
    });

    it('does not mutate the parent bindings when child adds fields', async () => {
      setEnv('NODE_ENV', 'production');

      const { logger } = await freshLogger();
      logger.child({ sessionId: 'sess-1' });

      logger.info('parent.line');

      const line = logSpy.mock.calls[0]?.[0] as string;
      const parsed = JSON.parse(line) as Record<string, unknown>;
      expect(parsed['sessionId']).toBeUndefined();
    });
  });

  describe('error()', () => {
    it('serialises an Error into name/message/stack', async () => {
      setEnv('NODE_ENV', 'production');

      const { logger } = await freshLogger();
      const err = new Error('boom');
      logger.error('op.failed', err, { sessionId: 'x' });

      const line = errorSpy.mock.calls[0]?.[0] as string;
      const parsed = JSON.parse(line) as { err: { name: string; message: string; stack?: string } };
      expect(parsed.err.name).toBe('Error');
      expect(parsed.err.message).toBe('boom');
      expect(parsed.err.stack).toContain('Error: boom');
    });

    it('treats a plain field bag as the second arg when no Error passed', async () => {
      setEnv('NODE_ENV', 'production');

      const { logger } = await freshLogger();
      logger.error('op.expected_failure', { reason: 'rate_limited' });

      const line = errorSpy.mock.calls[0]?.[0] as string;
      const parsed = JSON.parse(line) as Record<string, unknown>;
      expect(parsed['reason']).toBe('rate_limited');
      expect(parsed['err']).toBeUndefined();
    });
  });

  describe('safe stringify', () => {
    it('serialises BigInt without throwing', async () => {
      setEnv('NODE_ENV', 'production');

      const { logger } = await freshLogger();
      logger.info('tick', { tickId: 42n });

      const line = logSpy.mock.calls[0]?.[0] as string;
      // BigInt becomes `"42n"` — preserves the value AND that it was a bigint.
      expect(line).toContain('"42n"');
    });

    it('handles circular references without throwing', async () => {
      setEnv('NODE_ENV', 'production');

      const { logger } = await freshLogger();
      const obj: Record<string, unknown> = { name: 'x' };
      obj['self'] = obj;

      expect(() => {
        logger.info('circ', { obj });
      }).not.toThrow();
      const line = logSpy.mock.calls[0]?.[0] as string;
      expect(line).toContain('[Circular]');
    });
  });

  describe('Sentry forwarding', () => {
    it('does not call Sentry when SENTRY_DSN is unset', async () => {
      setEnv('NODE_ENV', 'production');
      delete process.env['SENTRY_DSN'];

      const { logger } = await freshLogger();
      logger.error('op.failed', new Error('boom'));

      // Resolve the lazy promise inside the logger.
      await new Promise((resolve) => {
        setImmediate(resolve);
      });
      expect(captureExceptionMock).not.toHaveBeenCalled();
      expect(captureMessageMock).not.toHaveBeenCalled();
    });

    it('forwards Error to captureException when DSN is set', async () => {
      setEnv('NODE_ENV', 'production');
      process.env['SENTRY_DSN'] = 'https://example.ingest.sentry.io/test';

      const { logger } = await freshLogger();
      const err = new Error('boom');
      logger.error('op.failed', err);

      await new Promise((resolve) => {
        setImmediate(resolve);
      });
      expect(captureExceptionMock).toHaveBeenCalledWith(
        err,
        expect.objectContaining({ extra: expect.objectContaining({ event: 'op.failed' }) }),
      );
    });

    it('forwards bare warn/error events to captureMessage', async () => {
      setEnv('NODE_ENV', 'production');
      process.env['SENTRY_DSN'] = 'https://example.ingest.sentry.io/test';

      const { logger } = await freshLogger();
      logger.warn('rate.limit.hit', { route: '/api/x' });

      await new Promise((resolve) => {
        setImmediate(resolve);
      });
      expect(captureMessageMock).toHaveBeenCalledWith(
        'rate.limit.hit',
        expect.objectContaining({ level: 'warning' }),
      );
    });

    it('does not forward info/debug to Sentry', async () => {
      setEnv('NODE_ENV', 'production');
      process.env['SENTRY_DSN'] = 'https://example.ingest.sentry.io/test';

      const { logger } = await freshLogger();
      logger.info('boot');
      logger.debug('detail');

      await new Promise((resolve) => {
        setImmediate(resolve);
      });
      expect(captureExceptionMock).not.toHaveBeenCalled();
      expect(captureMessageMock).not.toHaveBeenCalled();
    });
  });
});
