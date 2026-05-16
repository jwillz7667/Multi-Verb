# Architecture Decision Records (ADRs)

We use lightweight ADRs to capture non-trivial architectural decisions. Each ADR is
a short markdown file under this directory, numbered sequentially.

## When to write one

Write an ADR when a change:

- crosses service boundaries,
- modifies the shared domain model (`schemas/`),
- changes a quality gate,
- introduces a new runtime dependency on a third-party service, or
- locks the team into a non-obvious tradeoff (storage layout, schema design, transport choice, etc.).

If you're not sure, write one. ADRs are cheap; rediscovering forgotten context is expensive.

## Format

Use [`template.md`](./template.md) as the starting point. Keep ADRs under one page.

Each ADR has the following sections:

- **Context** — what is the situation that forced this decision
- **Decision** — what was decided
- **Consequences** — what becomes easier, what becomes harder

## Lifecycle

ADRs are immutable once merged to `main`. If a decision is later changed:

1. Write a new ADR that supersedes the old one.
2. Update the old ADR's status to `Superseded by ADR-NNNN`.
3. Link both directions.

## Naming

`NNNN-kebab-case-title.md`, where `NNNN` is the next sequence number, zero-padded to four digits.

## Index

| # | Title | Status |
|---|---|---|
| [0001](./0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](./0002-stack.md) | Stack — Vercel for web, Railway for engine + data, drop Supabase | Accepted |

(Update this index whenever you add an ADR.)
