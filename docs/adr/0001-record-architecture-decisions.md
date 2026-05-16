# ADR 0001: Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-05-16
- **Deciders:** Engineering team
- **Related:** brief §13 (Quality gates), `CONTRIBUTING.md` §7

## Context

Verbio is a system with strong product principles (bias toward silence,
full auditability) and a deliberate two-language architecture (Python engine

- TypeScript web). Several non-obvious tradeoffs are already baked into the
  brief: per-session engine processes, Pydantic-as-source-of-truth, persistence
  before execution, etc.

As the team grows and the codebase matures, decisions of this weight will
keep accumulating. Without a lightweight record:

- New joiners cannot tell which patterns are deliberate vs. accidental.
- Old decisions get re-litigated in code review without the original context.
- The brief grows unreadable if we try to embed every decision in it.

## Decision

We adopt Architecture Decision Records (ADRs) following the convention
described in `docs/adr/README.md`. ADRs are short markdown files, numbered
sequentially, capturing the **context**, **decision**, and **consequences**
of any change that:

- crosses service boundaries,
- modifies the shared domain model,
- changes a quality gate, or
- introduces a new runtime dependency.

ADRs are immutable once merged to `main`. Superseding an ADR requires a new
ADR that explicitly references the old one.

## Alternatives considered

- **Embed all decisions in the brief.** Rejected — the brief becomes a sprawling
  reference that nobody reads end-to-end, and version history is harder to
  reason about.
- **Use a Notion / Confluence space.** Rejected for now — keeping decisions in
  the repo means they version with the code and are visible to anyone reading
  the codebase. Revisit if we outgrow markdown.
- **No formal record (Slack / commit messages).** Rejected — context evaporates,
  and search across these surfaces is poor at depth.

## Consequences

**Easier:**

- Onboarding: a new engineer can read `docs/adr/` and understand the deliberate
  choices in the architecture.
- Code review: reviewers can challenge decisions by writing a counter-ADR rather
  than arguing in comments.
- Drift detection: an ADR that no longer holds is a signal that something needs
  to change architecturally.

**Harder:**

- Slightly more friction on non-trivial changes (must write an ADR).
- Risk of ADR sprawl if the bar for "non-trivial" creeps down. Mitigate by
  reviewing ADRs as a team periodically.

**Implied follow-up:**

- ADRs for the deliberate choices already encoded in the brief — engine
  process model, Pydantic source of truth, two-language split, etc. — will
  be written as the corresponding code lands.

## References

- [Michael Nygard — Documenting Architecture Decisions](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions.html)
- `verbio-engineering-brief.md`
- `docs/architecture.md`
