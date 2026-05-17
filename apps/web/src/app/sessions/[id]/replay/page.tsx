/**
 * /sessions/[id]/replay — replay & analysis mode (brief §11.2).
 *
 * Server component: authenticates, hydrates the session + the
 * supporting collections the replay shell renders against, and hands
 * a JSON-serializable bootstrap to the client component. The shell
 * then takes over for scrubber state and on-demand fetches.
 *
 * Bootstrap composition (concurrent — each call is independent):
 *   - the session row with recording fields (composite key + the
 *     identity→key map for per-participant tracks, surfaced as
 *     boolean / identity-list flags so keys never leave the server),
 *   - the participant roster (5 lanes for speech bands + filter),
 *   - every decision in the session (≤2,000 — covers ~16 min at 2 Hz,
 *     the brief's worst-case session is 60 min so we'll page in L5
 *     when the timeline needs it),
 *   - every flag in the session (≤500 — flags are sparse),
 *   - an initial window of utterances + snapshots so the page is
 *     interactive on first paint (L5–L7 will fetch deeper chunks on
 *     scrub / on demand).
 *
 * Empty session: a session that never went live (`actualStart`
 * null) still renders the shell so the researcher sees a clear "not
 * started" affordance instead of a 404.
 */

import { notFound, redirect } from 'next/navigation';

import { parseParticipantRecordingUrls } from '@/features/recordings';
import {
  findSessionForReplay,
  listDecisionsByRange,
  listParticipantsForSession,
  listSessionFlagsByRange,
  listSnapshotsByRange,
  listUtterancesByRange,
} from '@/features/sessions';
import type {
  DecisionRow,
  ParticipantRow,
  ReplaySessionRow,
  SessionFlagRow,
  StateSnapshotRow,
  UtteranceWithSpeakerRow,
} from '@/features/sessions';
import { auth } from '@/lib/auth';
import { orgIdForUser } from '@/lib/identity';
import { scopedDb } from '@/lib/scoped-db';

import { ReplayShell } from './replay-shell';

import type {
  ReplayBootstrap,
  ReplayDecisionDTO,
  ReplayFlagDTO,
  ReplayParticipantDTO,
  ReplaySessionDTO,
  ReplaySnapshotDTO,
  ReplayUtteranceDTO,
} from './replay-types';

export const dynamic = 'force-dynamic';

// Five-minute first-paint window. Sized to match the typical attention
// span on initial inspection — the L5 timeline will request deeper
// chunks as the researcher scrubs out of view.
const INITIAL_WINDOW_MS = 5 * 60 * 1000;

interface PageProps {
  params: Promise<{ id: string }>;
}

export default async function ReplayPage({ params }: PageProps): Promise<React.ReactElement> {
  const userSession = await auth();
  const { id } = await params;
  if (!userSession?.user?.id) {
    redirect(`/sign-in?callbackUrl=/sessions/${id}/replay`);
  }

  // Tenancy gate: scopedDb confirms ownership before any per-session
  // data is loaded. Cross-org access becomes a 404, indistinguishable
  // from a missing id — never reveal that another tenant owns the
  // session by serving its replay shell or even leaking timing info.
  const orgId = orgIdForUser(userSession.user.id);
  const owned = await scopedDb(orgId).sessions.findById(id);
  if (owned === null) notFound();

  const session = await findSessionForReplay(id);
  if (session === null) notFound();

  // Replay is an analysis affordance — only meaningful once the
  // session has finished. While it's `scheduled` or `live`, redirect
  // back to the live detail page (the brief's live view owns the
  // researcher controls). Researchers can always come back once the
  // session reaches `ended` or `aborted`.
  if (session.status !== 'ended' && session.status !== 'aborted') {
    redirect(`/sessions/${id}`);
  }

  // Initial window for utterances + snapshots. When the session hasn't
  // started we pass an empty window so the page renders with empty
  // panes rather than 5 minutes of pre-start nothing.
  const windowStart = session.actualStart ?? new Date(0);
  const windowEnd =
    session.actualStart === null
      ? new Date(0)
      : new Date(Math.min(windowStart.getTime() + INITIAL_WINDOW_MS, Date.now()));

  const [participants, decisions, flags, initialUtterances, initialSnapshots] = await Promise.all([
    listParticipantsForSession(id),
    listDecisionsByRange(id),
    listSessionFlagsByRange(id),
    session.actualStart === null
      ? Promise.resolve([])
      : listUtterancesByRange(id, { fromTs: windowStart, toTs: windowEnd }),
    session.actualStart === null
      ? Promise.resolve([])
      : listSnapshotsByRange(id, { fromTs: windowStart, toTs: windowEnd }),
  ]);

  const bootstrap: ReplayBootstrap = {
    session: serializeSession(session),
    participants: participants.map(serializeParticipant),
    decisions: decisions.map(serializeDecision),
    flags: flags.map(serializeFlag),
    initial_utterances: initialUtterances.map(serializeUtterance),
    initial_snapshots: initialSnapshots.map(serializeSnapshot),
  };

  return <ReplayShell bootstrap={bootstrap} />;
}

function serializeSession(row: ReplaySessionRow): ReplaySessionDTO {
  const perParticipant = parseParticipantRecordingUrls(row.perParticipantRecordingUrls);
  return {
    id: row.id,
    livekit_room_name: row.livekitRoomName,
    status: row.status,
    scheduled_start: row.scheduledStart?.toISOString() ?? null,
    actual_start: row.actualStart?.toISOString() ?? null,
    actual_end: row.actualEnd?.toISOString() ?? null,
    created_at: row.createdAt.toISOString(),
    has_composite_recording: row.recordingUrl !== null && row.recordingUrl !== '',
    participant_recording_identities: Object.keys(perParticipant).sort(),
  };
}

function serializeParticipant(row: ParticipantRow): ReplayParticipantDTO {
  return {
    id: row.id,
    display_name: row.displayName,
    livekit_identity: row.livekitIdentity,
    role: row.role,
    joined_at: row.joinedAt?.toISOString() ?? null,
    left_at: row.leftAt?.toISOString() ?? null,
  };
}

function serializeDecision(row: DecisionRow): ReplayDecisionDTO {
  return {
    id: row.id,
    // `tickId` is a bigint at the Prisma layer; the wire shape uses
    // number because JSON has no bigint. JavaScript safely handles
    // integers up to 2^53 — at 2 Hz that's ~143 million years of
    // ticks, so the lossy conversion is irrelevant.
    tick_id: Number(row.tickId),
    ts: row.ts.toISOString(),
    action: row.action,
    target_participant_id: row.targetParticipantId,
    source: row.source,
    triggering_rule: row.triggeringRule,
    researcher_id: row.researcherId,
    researcher_hint: row.researcherHint,
    reason_codes: row.reasonCodes,
    reason_human: row.reasonHuman,
    confidence: row.confidence,
    suppressed_by: row.suppressedBy,
    was_executed: row.wasExecuted,
    llm_prompt: row.llmPrompt,
    llm_output: row.llmOutput,
    tts_audio_url: row.ttsAudioUrl,
    spoken_at: row.spokenAt?.toISOString() ?? null,
    cooldown_until: row.cooldownUntil.toISOString(),
  };
}

function serializeFlag(row: SessionFlagRow): ReplayFlagDTO {
  return {
    id: row.id,
    ts: row.ts.toISOString(),
    researcher_id: row.researcherId,
    note: row.note,
    auto_generated: row.autoGenerated,
  };
}

function serializeUtterance(row: UtteranceWithSpeakerRow): ReplayUtteranceDTO {
  return {
    id: row.id,
    participant_id: row.participantId,
    participant_identity: row.participantIdentity,
    participant_display_name: row.participantDisplayName,
    text: row.text,
    is_final: row.isFinal,
    confidence: row.confidence,
    start_ts: row.startTs.toISOString(),
    end_ts: row.endTs.toISOString(),
  };
}

function serializeSnapshot(row: StateSnapshotRow): ReplaySnapshotDTO {
  return {
    id: row.id,
    tick_id: Number(row.tickId),
    ts: row.ts.toISOString(),
    state: row.state,
  };
}
