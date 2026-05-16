/**
 * Prisma client singleton, server-only.
 *
 * Next.js hot-reload re-imports server modules, so a naive
 * `new PrismaClient()` per module load would exhaust the Postgres
 * connection pool in dev. We cache the instance on `globalThis` in
 * non-production environments.
 *
 * Always import this module — never construct `PrismaClient` directly.
 */

import 'server-only';

import { PrismaClient } from '@/generated/prisma';

const globalForPrisma = globalThis as unknown as {
  prisma?: PrismaClient;
};

export const db: PrismaClient =
  globalForPrisma.prisma ??
  new PrismaClient({
    log: process.env.NODE_ENV === 'development' ? ['warn', 'error'] : ['error'],
  });

if (process.env.NODE_ENV !== 'production') {
  globalForPrisma.prisma = db;
}
