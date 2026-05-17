/**
 * Exports feature — public surface.
 *
 * Consumers (replay route handlers + the replay shell's export panel)
 * import from this barrel. Deep imports across feature boundaries
 * are forbidden per the project's architecture rules.
 */

export { buildTranscriptLines, formatTranscriptTxt, formatTranscriptVtt } from './transcript';
export type { TranscriptHeader, TranscriptLine } from './transcript';
export {
  DECISION_LOG_COLUMNS,
  formatDecisionLogCsv,
  formatDecisionLogHeader,
  formatDecisionLogRow,
} from './decision-log';
export type { DecisionLogColumn } from './decision-log';
export { formatSnapshotsJsonl, snapshotToJsonl } from './state-snapshots';
export type { StateSnapshotJsonl } from './state-snapshots';
export {
  buildFfmpegArgs,
  CLIP_AUDIO_BITRATE,
  computeClipWindow,
  DEFAULT_CLIP_PAD_SEC,
  makeClipFilename,
  spawnFfmpegClipStream,
} from './audio-clip';
export type { ClipExtractOptions, ClipExtractResult, ClipWindow, SpawnFn } from './audio-clip';
