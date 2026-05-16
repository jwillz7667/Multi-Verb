# Security Policy

Verbio is proprietary software owned by Viral Ventures LLC. Sessions handled by Verbio may contain personally identifiable information (PII) and may fall under Institutional Review Board (IRB) review.

## Reporting a vulnerability

If you discover a security vulnerability in Verbio, please report it **privately**:

- **Do not** open a public GitHub issue, discussion, or pull request that describes the vulnerability.
- **Do not** disclose the vulnerability publicly until Viral Ventures LLC has had a reasonable opportunity to remediate.
- Email security disclosures to: **`security@viralventures.dev`** *(replace with the real address before production)*

Include in your report:

- A description of the vulnerability and its impact.
- Step-by-step reproduction instructions.
- Any proof-of-concept code, scripts, or recordings.
- The commit SHA / deployed version where you observed the issue.
- Your name and contact information for follow-up (optional but appreciated).

We aim to:

- **Acknowledge receipt within 2 business days.**
- **Provide a remediation timeline within 7 business days.**
- **Notify you when the fix has shipped.**

---

## Scope

### In scope

- All code in this repository (`apps/web`, `services/engine`, `packages/*`, `infra/*`, `schemas/`).
- Verbio-operated production infrastructure (`verbio-web`, `verbio-engine`).
- Verbio-stored data on Railway Postgres, Railway Redis, and Cloudflare R2.
- Verbio CI/CD pipelines and release artifacts.

### Out of scope (report to the upstream vendor)

- Third-party services we depend on:
  - LiveKit Cloud → [LiveKit security](https://livekit.io/security)
  - Deepgram → [Deepgram security](https://deepgram.com/security)
  - Anthropic → [Anthropic responsible disclosure](https://www.anthropic.com/security)
  - Cartesia → vendor disclosure channel
  - ElevenLabs → vendor disclosure channel
  - Vercel → [Vercel security](https://vercel.com/security)
  - Railway → [Railway security](https://railway.com/legal/security)
  - Cloudflare (R2) → [Cloudflare HackerOne](https://hackerone.com/cloudflare)
  - Resend → vendor disclosure channel.

---

## Sensitive data handling

When working on Verbio, the following are treated as confidential by default:

- Session audio recordings (mixed and per-track).
- Transcripts (utterances, rolling transcripts, snapshots).
- Participant identifiers (`participant_id`, `display_name`, `livekit_identity`).
- Researcher identifiers and organizational metadata.
- API keys, service-role keys, signing secrets, and webhook secrets.
- LLM prompts and outputs when they contain participant content.

**Do not** paste real participant content into bug reports, Slack, or AI tools. Use the synthetic fixtures under `services/engine/tests/fixtures/` for examples.

---

## Coordinated disclosure

We follow coordinated disclosure. Please give Viral Ventures LLC a reasonable opportunity to remediate before any public discussion (typically 90 days from the date of remediation acknowledgment, sooner if the vulnerability is being actively exploited).

If you require a CVE for an unfixed vulnerability you have reported, we will work with you in good faith. If we have not responded within the SLA above, you may escalate to the contact on file in [LICENSE](./LICENSE).

---

## Security best-practices enforced in the repo

- Secrets live in environment variables only; never committed. `.env.example` documents required vars; `dotenv-safe` (web) and Pydantic settings (engine) validate presence at boot.
- Tenant isolation enforced at the application layer via the `scopedDb(orgId)` Prisma extension + lint rule banning direct `prisma.<model>` access. See brief §10.3 and ADR-0002. Cross-org integration tests gate every PR that touches the data layer.
- Conventional Commits + commitlint + lefthook prevent `--no-verify` slip-ups.
- Dependabot weekly updates for npm, pip, and GitHub Actions.
- Dependency scanning (GitHub Advanced Security) is enabled on the repository.
- No third-party analytics on session content.
- Audio storage encrypted at rest (Cloudflare R2 default AES-256; bucket-scoped customer-managed keys planned for Phase 7); retention controlled per study via R2 lifecycle policies.

---

## Cryptographic posture

- All transport: TLS 1.2+ (HTTPS, WSS).
- LiveKit room JWTs: short-lived (≤ 30 min), per-session, scoped to a single room.
- Web auth JWTs: Auth.js v5 (HS256 signed with `AUTH_SECRET`, rotated quarterly); 30-day refresh / 15-min access.
- Database credentials for the engine: a dedicated `verbio_engine` Postgres role with table-level grants (no `SUPERUSER`); stored only in Railway env config; never exposed to the browser.
- R2 access keys: scoped per environment; bucket access mediated via signed URLs with short TTLs (≤ 15 min for recordings, ≤ 60 min for exports).

---

## Contact

For all security matters:

- **Email:** `security@viralventures.dev` *(replace with real address before launch)*
- **Postal:** Viral Ventures LLC, Maple Grove, Minnesota, USA

For non-security inquiries, see [LICENSE](./LICENSE) §15.
