/**
 * Session feature — typed inputs and outputs.
 *
 * The Prisma model `ModeratedSession` is the wire shape, but routes
 * and forms speak Zod-validated DTOs that exclude server-managed
 * fields (id, status, timestamps). Keeps the boundary contract
 * explicit and self-documenting.
 */

import { z } from 'zod';

export const SESSION_STATUSES = ['scheduled', 'live', 'ended', 'aborted'] as const;
export type SessionStatus = (typeof SESSION_STATUSES)[number];

export const PARTICIPANT_ROLES = ['participant', 'researcher', 'moderator'] as const;
export type ParticipantRole = (typeof PARTICIPANT_ROLES)[number];

/**
 * Researcher-facing create form input. Room names are minted server-
 * side; the researcher only chooses an optional scheduled start.
 */
export const CreateSessionInputSchema = z.object({
  scheduledStart: z
    .union([z.string().datetime(), z.literal('')])
    .optional()
    .transform((v) => (v && v !== '' ? new Date(v) : null)),
});
export type CreateSessionInput = z.infer<typeof CreateSessionInputSchema>;

export interface CreatedSession {
  id: string;
  livekitRoomName: string;
  status: SessionStatus;
  scheduledStart: Date | null;
  createdAt: Date;
}

export const MintTokenInputSchema = z.object({
  sessionId: z.string().uuid('sessionId must be a UUID'),
  displayName: z.string().trim().min(1, 'displayName is required').max(80),
  role: z.enum(PARTICIPANT_ROLES),
});
export type MintTokenInput = z.infer<typeof MintTokenInputSchema>;
