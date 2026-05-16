# Cloudflare R2 — recording storage

Verbio stores mixed + per-participant audio recordings in Cloudflare R2.
R2 is S3-compatible, so any AWS SDK works; we use `@aws-sdk/client-s3`
in `apps/web` and `boto3` in `services/engine`.

## Layout

```
infra/r2/
├── README.md            # this file
└── lifecycle.json       # bucket lifecycle policy (committed; applied via CLI)
```

## Why R2 (not Supabase Storage or S3)

- **Egress is free.** Replaying a 60-minute session can mean
  re-downloading the audio plus per-participant tracks; on S3 the
  egress cost would dominate. R2 charges only for storage + class-A
  requests.
- **S3-compatible.** Drop-in for `@aws-sdk/client-s3`; signed URLs
  work the same way.
- **Co-located edge.** Cloudflare's network puts the bucket near
  whatever region the researcher's browser hits.

## Bucket conventions

- One bucket per Vercel environment: `verbio-recordings-{preview,
staging,production}`. The bucket name is supplied via `R2_BUCKET`
  in each environment's settings.
- Object keys:
  - `recordings/{session_id}/mixed.mp4`
  - `recordings/{session_id}/participant-{participant_id}.opus`
  - `exports/{session_id}/transcript.json`
  - `exports/{session_id}/decisions.csv`

## Lifecycle policy

`lifecycle.json` applies the following rules (apply with the
Cloudflare R2 CLI: `wrangler r2 bucket lifecycle put <bucket-name>
--config infra/r2/lifecycle.json`):

| Rule                                       | Prefix        | Action                                  |
| ------------------------------------------ | ------------- | --------------------------------------- |
| `expire-incomplete-multipart-uploads`      | (all)         | Abort multipart uploads stale > 7 days  |
| `transition-recordings-to-cold-storage`    | `recordings/` | Move to Infrequent Access after 30 days |
| `expire-recordings-after-retention-window` | `recordings/` | Delete after 730 days (2 years)         |
| `expire-exports-after-90-days`             | `exports/`    | Delete after 90 days                    |

The 2-year recording retention reflects the typical IRB / research
ethics commitment in the customer's own data agreements. Tighten or
loosen per study via per-object lifecycle headers when needed.

## Signing URL TTLs

Signed URLs issued by `apps/web` should default to 5 minutes. Replay
sessions that need longer-lived URLs should re-sign on the fly via
the SSE channel — never issue a multi-hour URL.

## Phase 0 done-when

- Bucket exists in Cloudflare (manual; outside this scaffold).
- Lifecycle policy applied from this committed JSON.
- `R2_*` env vars set in Vercel project settings per environment.
- No code yet writes to R2; the wiring lands in Phase 5 with the
  recording pipeline.
