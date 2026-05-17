/**
 * Exports feature — public surface.
 *
 * Consumers (replay route handlers + the replay shell's export panel)
 * import from this barrel. Deep imports across feature boundaries
 * are forbidden per the project's architecture rules.
 */

export { buildTranscriptLines, formatTranscriptTxt, formatTranscriptVtt } from './transcript';
export type { TranscriptHeader, TranscriptLine } from './transcript';
