'use client';

/**
 * Persona form fields — the controlled inputs for `moderator_persona`.
 *
 * Extracted from the new/edit page shells so the same control surface
 * is used everywhere a persona is edited (today: new + edit; later:
 * the per-session quick-tweak panel). The component is a pure
 * controlled input that bubbles a typed `value` change up to the
 * parent, which owns submit state and API plumbing.
 */

import {
  listVoicesForProvider,
  PERSONA_FORMALITIES,
  PERSONA_TONES,
  VOICE_PROVIDERS,
} from '@/features/studies';
import type {
  ModeratorPersonaInput,
  PersonaFormality,
  PersonaTone,
  VoiceProvider,
} from '@/features/studies';

interface Props {
  value: ModeratorPersonaInput;
  onChange: (next: ModeratorPersonaInput) => void;
  disabled?: boolean;
}

export function PersonaFormFields({ value, onChange, disabled }: Props): React.ReactElement {
  const voicesForProvider = listVoicesForProvider(value.voice_provider);
  const selectedVoice = voicesForProvider.find((v) => v.voiceId === value.voice_id);

  return (
    <fieldset className="border-border-default bg-surface-primary flex flex-col gap-4 rounded-lg border p-6">
      <legend className="px-2 text-sm font-medium">Moderator persona</legend>

      <label className="flex flex-col gap-2 text-sm">
        <span className="text-text-primary font-medium">Style prompt</span>
        <textarea
          value={value.style_prompt}
          onChange={(e) => {
            onChange({ ...value, style_prompt: e.target.value });
          }}
          disabled={disabled}
          maxLength={500}
          rows={3}
          placeholder="You are a careful moderator. You stay quiet most of the time and only intervene to gently prompt unheard participants or redirect drifting topics."
          className="border-border-default rounded-md border bg-transparent px-3 py-2 text-sm leading-relaxed disabled:opacity-60"
        />
        <span className="text-text-tertiary text-xs">
          Prepended verbatim to the system prompt the LLM gets. Keep it short (1–2 sentences) and
          describe how the moderator should <em>sound</em>, not <em>what</em> it should say.
        </span>
      </label>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <label className="flex flex-col gap-2 text-sm">
          <span className="text-text-primary font-medium">Tone</span>
          <select
            value={value.tone}
            onChange={(e) => {
              onChange({ ...value, tone: e.target.value as PersonaTone });
            }}
            disabled={disabled}
            className="border-border-default rounded-md border bg-transparent px-3 py-2 text-sm disabled:opacity-60"
          >
            {PERSONA_TONES.map((tone) => (
              <option key={tone} value={tone}>
                {tone}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-2 text-sm">
          <span className="text-text-primary font-medium">Formality</span>
          <select
            value={value.formality}
            onChange={(e) => {
              onChange({ ...value, formality: e.target.value as PersonaFormality });
            }}
            disabled={disabled}
            className="border-border-default rounded-md border bg-transparent px-3 py-2 text-sm disabled:opacity-60"
          >
            {PERSONA_FORMALITIES.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <label className="flex flex-col gap-2 text-sm">
          <span className="text-text-primary font-medium">Voice provider</span>
          <select
            value={value.voice_provider}
            onChange={(e) => {
              const provider = e.target.value as VoiceProvider;
              const firstVoice = listVoicesForProvider(provider)[0];
              onChange({
                ...value,
                voice_provider: provider,
                // Reset voice when the provider changes — the previously
                // selected id belongs to a different library and would
                // round-trip as `UnknownVoiceError` from the engine.
                voice_id: firstVoice?.voiceId ?? '',
              });
            }}
            disabled={disabled}
            className="border-border-default rounded-md border bg-transparent px-3 py-2 text-sm disabled:opacity-60"
          >
            {VOICE_PROVIDERS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
          <span className="text-text-tertiary text-xs">
            Default: Cartesia (lowest latency, brief §9). ElevenLabs Flash is the fallback voice.
          </span>
        </label>

        <label className="flex flex-col gap-2 text-sm">
          <span className="text-text-primary font-medium">Voice</span>
          <select
            value={value.voice_id}
            onChange={(e) => {
              onChange({ ...value, voice_id: e.target.value });
            }}
            disabled={disabled === true || voicesForProvider.length === 0}
            className="border-border-default rounded-md border bg-transparent px-3 py-2 text-sm disabled:opacity-60"
          >
            {voicesForProvider.map((voice) => (
              <option key={`${voice.provider}:${voice.voiceId}`} value={voice.voiceId}>
                {voice.displayName} ({voice.formality}, {voice.warmth}, {voice.pace})
              </option>
            ))}
          </select>
        </label>
      </div>

      {selectedVoice !== undefined && (
        <p className="text-text-secondary border-border-default bg-surface-tertiary rounded-md border px-3 py-2 text-xs">
          {selectedVoice.description}
        </p>
      )}
    </fieldset>
  );
}
