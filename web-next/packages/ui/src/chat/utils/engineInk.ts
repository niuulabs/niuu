export type Engine = 'claude' | 'codex' | 'other';

const CLAUDE_MODEL = /claude|fable|opus|sonnet|haiku/;
/** OpenAI names: `gpt-*`, `codex-*`, and the o-series (`o1`, `o3-mini`, `o4-mini`...). */
const CODEX_MODEL = /gpt|codex|^o\d/;

const ENGINE_INK: Record<Engine, string> = {
  claude: 'var(--color-harness-claude)',
  codex: 'var(--color-harness-codex)',
  other: 'var(--color-brand)',
};

/** Which engine family a model id belongs to; provider prefixes (`openai/gpt-5`) are ignored. */
export function engineOf(model: string | undefined): Engine {
  if (!model) return 'other';
  const id = (model.split('/').at(-1) ?? model).toLowerCase();
  if (CLAUDE_MODEL.test(id)) return 'claude';
  if (CODEX_MODEL.test(id)) return 'codex';
  return 'other';
}

/** The design-token colour for a model's speaker dot and name. */
export function engineInk(model: string | undefined): string {
  return ENGINE_INK[engineOf(model)];
}
