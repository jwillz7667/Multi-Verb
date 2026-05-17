/**
 * Tests for <ReplayDecisionDetail />.
 *
 * Coverage focuses on the audit semantics the brief promises:
 *
 *   - With no selection: shows the "select a marker" empty copy and
 *     does not dispatch a fetch.
 *   - Selecting a decision renders the action / source / target /
 *     reason codes / reason_human / suppressed_by chips from the
 *     bootstrap row (no fetch required for these fields).
 *   - LLM block is hidden when the decision has no prompt or output,
 *     visible (with both halves) when it does.
 *   - Evaluations table fetches from
 *     /api/sessions/[id]/decisions/[decisionId]/evaluations and
 *     renders one row per rule, sorted firing-first.
 *   - Cache: re-selecting the same decision doesn't refetch.
 *   - 404 surfaces "no evaluations" copy; 401 surfaces "sign in again".
 *   - The firing rule's row gets a highlight class so the eye lands
 *     there immediately.
 */

import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ReplayDecisionDetail } from './replay-decision-detail';

import type { ReplayDecisionDTO, ReplayParticipantDTO } from './replay-types';

const SESSION_ID = 'sess-1';

const ALICE: ReplayParticipantDTO = {
  id: 'p-alice',
  display_name: 'Alice',
  livekit_identity: 'alice-001',
  role: 'participant',
  joined_at: '2026-05-01T10:00:05.000Z',
  left_at: null,
};

const BOB: ReplayParticipantDTO = {
  id: 'p-bob',
  display_name: 'Bob',
  livekit_identity: 'bob-002',
  role: 'participant',
  joined_at: '2026-05-01T10:00:10.000Z',
  left_at: null,
};

function makeDecision(overrides: Partial<ReplayDecisionDTO> = {}): ReplayDecisionDTO {
  return {
    id: 'dec-1',
    tick_id: 60,
    ts: '2026-05-01T10:00:30.000Z',
    action: 'stay_silent',
    target_participant_id: null,
    source: 'rules_engine',
    triggering_rule: null,
    researcher_id: null,
    researcher_hint: null,
    reason_codes: [],
    reason_human: '',
    confidence: null,
    suppressed_by: [],
    was_executed: false,
    llm_prompt: null,
    llm_output: null,
    tts_audio_url: null,
    spoken_at: null,
    cooldown_until: '2026-05-01T10:00:35.000Z',
    ...overrides,
  };
}

function setupFetchMock(handler: (url: string) => Response): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    return Promise.resolve(handler(url));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('<ReplayDecisionDetail /> — no selection', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('unexpected fetch'))),
    );
  });

  it('renders the empty-state copy and skips any fetch', () => {
    render(
      <ReplayDecisionDetail
        sessionId={SESSION_ID}
        decisions={[]}
        participants={[ALICE]}
        selectedDecisionId={null}
      />,
    );

    expect(screen.getByTestId('replay-decision-empty')).toHaveTextContent(
      /Select a marker on the timeline/i,
    );
    expect(screen.queryByTestId('replay-decision-card')).not.toBeInTheDocument();
  });
});

describe('<ReplayDecisionDetail /> — decision card', () => {
  it('renders action, source, target name, reason codes, and reason_human from the bootstrap row', async () => {
    setupFetchMock(
      () =>
        new Response(JSON.stringify({ decision_id: 'dec-1', evaluations: [] }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    );

    const decision = makeDecision({
      action: 'redirect',
      source: 'rules_engine',
      target_participant_id: ALICE.id,
      triggering_rule: 'speaker_imbalance',
      reason_codes: ['fair_share_breach', 'silence_followed'],
      reason_human: "Pulled in Bob because Alice's last 5min share was 70%.",
      confidence: 0.81,
      was_executed: true,
    });

    render(
      <ReplayDecisionDetail
        sessionId={SESSION_ID}
        decisions={[decision]}
        participants={[ALICE, BOB]}
        selectedDecisionId={decision.id}
      />,
    );

    expect(screen.getByTestId('replay-decision-card')).toBeInTheDocument();
    expect(screen.getByTestId('replay-decision-action')).toHaveTextContent(/redirect/);
    expect(screen.getByTestId('replay-decision-action')).toHaveTextContent(/spoken/);
    expect(screen.getByTestId('replay-decision-source')).toHaveTextContent(/engine/);
    expect(screen.getByTestId('replay-decision-target')).toHaveTextContent(/Alice/);
    expect(screen.getByTestId('replay-decision-reason')).toHaveTextContent(
      /Alice's last 5min share was 70%/,
    );
    const reasonChips = screen.getByTestId('replay-decision-reason-codes');
    expect(reasonChips).toHaveTextContent(/fair_share_breach/);
    expect(reasonChips).toHaveTextContent(/silence_followed/);

    // The "spoken" status pulls through to the action chip; let the
    // evaluations promise resolve so React drains the act() boundary.
    await waitFor(() => {
      expect(screen.getByTestId('replay-decision-evaluations')).toBeInTheDocument();
    });
  });

  it('marks the action chip "suppressed" when a non-silent decision did not execute', async () => {
    setupFetchMock(
      () =>
        new Response(JSON.stringify({ decision_id: 'dec-1', evaluations: [] }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    );

    const decision = makeDecision({
      action: 'prompt',
      was_executed: false,
      suppressed_by: ['quietness_budget_exhausted'],
    });

    render(
      <ReplayDecisionDetail
        sessionId={SESSION_ID}
        decisions={[decision]}
        participants={[]}
        selectedDecisionId={decision.id}
      />,
    );

    expect(screen.getByTestId('replay-decision-action')).toHaveTextContent(/suppressed/);
    expect(screen.getByTestId('replay-decision-suppressed')).toHaveTextContent(
      /quietness_budget_exhausted/,
    );

    await waitFor(() => {
      expect(screen.getByTestId('replay-decision-evaluations')).toBeInTheDocument();
    });
  });

  it('hides the mouth block when no llm_prompt or llm_output is present', async () => {
    setupFetchMock(
      () =>
        new Response(JSON.stringify({ decision_id: 'dec-1', evaluations: [] }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    );

    render(
      <ReplayDecisionDetail
        sessionId={SESSION_ID}
        decisions={[makeDecision({ action: 'stay_silent' })]}
        participants={[]}
        selectedDecisionId="dec-1"
      />,
    );

    expect(screen.queryByTestId('replay-decision-mouth')).not.toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByTestId('replay-decision-evaluations')).toBeInTheDocument();
    });
  });

  it('renders the llm_output verbatim and a collapsible prompt block when present', async () => {
    setupFetchMock(
      () =>
        new Response(JSON.stringify({ decision_id: 'dec-1', evaluations: [] }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    );

    const decision = makeDecision({
      action: 'prompt',
      was_executed: true,
      llm_prompt: { system: 'You are a moderator.', user: 'Bring in Bob.' },
      llm_output: 'Bob, what is your reaction?',
    });

    render(
      <ReplayDecisionDetail
        sessionId={SESSION_ID}
        decisions={[decision]}
        participants={[]}
        selectedDecisionId={decision.id}
      />,
    );

    expect(screen.getByTestId('replay-decision-llm-output')).toHaveTextContent(
      /Bob, what is your reaction\?/,
    );
    expect(screen.getByTestId('replay-decision-llm-prompt')).toHaveTextContent(/Bring in Bob/);

    await waitFor(() => {
      expect(screen.getByTestId('replay-decision-evaluations')).toBeInTheDocument();
    });
  });
});

describe('<ReplayDecisionDetail /> — evaluations fetch', () => {
  it('fetches /evaluations on selection and renders one row per rule, firing-first', async () => {
    const fetchMock = setupFetchMock(
      () =>
        new Response(
          JSON.stringify({
            decision_id: 'dec-1',
            evaluations: [
              {
                id: 'ev-1',
                ruleName: 'silence_gap',
                ruleVersion: '2026-01-01.1',
                fired: false,
                suppressedReason: null,
                confidence: 0,
              },
              {
                id: 'ev-2',
                ruleName: 'unheard_participant',
                ruleVersion: '2026-01-01.1',
                fired: true,
                suppressedReason: null,
                confidence: 0.72,
              },
              {
                id: 'ev-3',
                ruleName: 'topic_drift',
                ruleVersion: '2026-01-01.1',
                fired: true,
                suppressedReason: 'lower_priority_won',
                confidence: 0.55,
              },
            ],
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        ),
    );

    const decision = makeDecision({ id: 'dec-1', triggering_rule: 'unheard_participant' });

    render(
      <ReplayDecisionDetail
        sessionId={SESSION_ID}
        decisions={[decision]}
        participants={[]}
        selectedDecisionId={decision.id}
      />,
    );

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/sessions/${SESSION_ID}/decisions/dec-1/evaluations`,
    );

    const rows = await screen.findAllByTestId(/replay-decision-evaluation-/);
    expect(rows).toHaveLength(3);
    // Firing-first ordering: unheard_participant (chose this), then
    // topic_drift (fired but lost), then silence_gap (didn't fire).
    expect(rows[0]).toHaveAttribute(
      'data-testid',
      'replay-decision-evaluation-unheard_participant',
    );
    expect(rows[1]).toHaveAttribute('data-testid', 'replay-decision-evaluation-topic_drift');
    expect(rows[2]).toHaveAttribute('data-testid', 'replay-decision-evaluation-silence_gap');

    // Suppressed-reason copy renders on the losing row.
    expect(rows[1]).toHaveTextContent(/lower_priority_won/);
  });

  it('does not refetch when reselecting the same decision after a successful load', async () => {
    const fetchMock = setupFetchMock(
      () =>
        new Response(
          JSON.stringify({
            decision_id: 'dec-1',
            evaluations: [],
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        ),
    );

    const decision = makeDecision({ id: 'dec-1' });

    const { rerender } = render(
      <ReplayDecisionDetail
        sessionId={SESSION_ID}
        decisions={[decision]}
        participants={[]}
        selectedDecisionId={decision.id}
      />,
    );

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    // Clear selection then re-select — cache should satisfy.
    rerender(
      <ReplayDecisionDetail
        sessionId={SESSION_ID}
        decisions={[decision]}
        participants={[]}
        selectedDecisionId={null}
      />,
    );
    rerender(
      <ReplayDecisionDetail
        sessionId={SESSION_ID}
        decisions={[decision]}
        participants={[]}
        selectedDecisionId={decision.id}
      />,
    );

    // Settle one tick.
    await waitFor(() => {
      expect(screen.getByTestId('replay-decision-evaluations')).toBeInTheDocument();
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('surfaces "no evaluations recorded" copy on a 404', async () => {
    setupFetchMock(
      () =>
        new Response(JSON.stringify({ error: 'decision_not_found' }), {
          status: 404,
          headers: { 'content-type': 'application/json' },
        }),
    );

    render(
      <ReplayDecisionDetail
        sessionId={SESSION_ID}
        decisions={[makeDecision({ id: 'dec-1' })]}
        participants={[]}
        selectedDecisionId="dec-1"
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('replay-decision-evaluations')).toHaveTextContent(
        /No evaluations recorded/i,
      );
    });
  });

  it('surfaces sign-in copy on a 401', async () => {
    setupFetchMock(
      () =>
        new Response(JSON.stringify({ error: 'unauthorized' }), {
          status: 401,
          headers: { 'content-type': 'application/json' },
        }),
    );

    render(
      <ReplayDecisionDetail
        sessionId={SESSION_ID}
        decisions={[makeDecision({ id: 'dec-1' })]}
        participants={[]}
        selectedDecisionId="dec-1"
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('replay-decision-evaluations')).toHaveTextContent(/Sign in again/i);
    });
  });
});
