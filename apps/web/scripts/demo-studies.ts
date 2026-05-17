/**
 * End-to-end demo: studies feature against the live Railway DB.
 *
 * Drives `features/studies` directly (no HTTP, no auth) so we can prove
 * the CRUD path round-trips the moderator persona JSONB correctly.
 * Same code path the route handlers call; only auth + the Next.js
 * request envelope are skipped.
 *
 *   pnpm tsx scripts/demo-studies.ts
 *
 * Loads `.env.local` first (Next.js precedence: .env.local > .env).
 * Uses dynamic imports because ESM hoists static imports above
 * imperative code — `@/lib/env` validates at module load, so env must
 * be in place before the first import that pulls it in.
 */

process.loadEnvFile('.env.local');
try {
  process.loadEnvFile('.env');
} catch {
  /* .env is optional locally; .env.local already has everything */
}

async function main(): Promise<void> {
  const { createNewStudy, getStudy, listStudies, updateExistingStudy } =
    await import('@/features/studies');
  const { db } = await import('@/lib/db');
  const { orgIdForUser, userUuidForAudit } = await import('@/lib/identity');

  const DEMO_USER = 'demo-user-jwillz';
  const orgId = orgIdForUser(DEMO_USER);
  const createdBy = userUuidForAudit(DEMO_USER);

  console.log('━'.repeat(72));
  console.log('Verbio — studies feature demo (live Railway DB)');
  console.log('━'.repeat(72));
  console.log(`derived orgId    = ${orgId}`);
  console.log(`derived createdBy = ${createdBy}`);
  console.log();

  console.log('1) list studies (before)');
  const before = await listStudies(orgId);
  console.log(`   ${before.length} existing studies for this org`);
  console.log();

  console.log('2) create a new study with full moderator persona');
  const created = await createNewStudy({
    orgId,
    createdBy,
    input: {
      name: 'Q2 banking pilot — autopay friction',
      prompt:
        'How do you currently manage recurring subscriptions, and what would make autopay setup easier?',
      moderatorPersona: {
        style_prompt:
          'You are a careful moderator who stays quiet unless a participant has gone silent or the group has drifted off-topic.',
        tone: 'warm',
        formality: 'neutral',
        voice_provider: 'cartesia',
        voice_id: '156fb8d2-335b-4950-9cb3-a2d33befec77',
      },
    },
  });
  console.log(`   created id      = ${created.id}`);
  console.log(`   name            = ${created.name}`);
  console.log(`   rulesVersion    = ${created.rulesVersion}`);
  console.log(`   persona.tone    = ${created.moderatorPersona.tone}`);
  console.log(
    `   persona.voice   = ${created.moderatorPersona.voice_provider}/${created.moderatorPersona.voice_id}`,
  );
  console.log();

  console.log('3) read back via getStudy (org-scoped) — proves the JSONB round-trip');
  const fetched = await getStudy(created.id, orgId);
  if (fetched === null) throw new Error('getStudy returned null right after create');
  console.log(`   persona.style_prompt =`);
  console.log(`     "${fetched.moderatorPersona.style_prompt}"`);
  console.log();

  console.log('4) cross-tenant smuggling guard — different orgId must return null');
  const wrongOrg = await getStudy(created.id, orgIdForUser('someone-else'));
  console.log(`   getStudy(realId, wrongOrg) = ${wrongOrg === null ? 'null ✓' : 'LEAK ✗'}`);
  console.log();

  console.log('5) PATCH — change tone + voice via updateExistingStudy');
  const updated = await updateExistingStudy({
    studyId: created.id,
    orgId,
    patch: {
      moderatorPersona: {
        ...created.moderatorPersona,
        tone: 'professional',
        voice_provider: 'elevenlabs',
        voice_id: 'XB0fDUnXU5powFXDhCwa',
      },
    },
  });
  console.log(`   persona.tone    = ${updated.moderatorPersona.tone}`);
  console.log(
    `   persona.voice   = ${updated.moderatorPersona.voice_provider}/${updated.moderatorPersona.voice_id}`,
  );
  console.log();

  console.log('6) list studies (after)');
  const after = await listStudies(orgId);
  console.log(`   ${after.length} studies — sorted createdAt desc:`);
  for (const s of after.slice(0, 5)) {
    console.log(
      `     ${s.id}  ${s.name}  [${s.moderatorPersona.tone}/${s.moderatorPersona.voice_provider}]`,
    );
  }
  console.log();

  console.log('━'.repeat(72));
  console.log('Demo complete — leaving the created row in place for browser inspection.');
  console.log(`Open http://localhost:3000/studies/${created.id} once signed in.`);
  console.log('━'.repeat(72));

  await db.$disconnect();
}

main().catch((err: unknown) => {
  console.error(err);
  process.exit(1);
});
