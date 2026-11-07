import { describe, expect, it } from 'vitest';
import { engineInk, engineOf } from './engineInk';

describe('engineInk', () => {
  it.each([
    'claude-sonnet-5-5',
    'claude-fable-5',
    'opus',
    'Sonnet',
    'haiku-4',
    'anthropic/claude-opus-4',
  ])('colours %s as Claude', (model) => {
    expect(engineOf(model)).toBe('claude');
    expect(engineInk(model)).toBe('var(--color-harness-claude)');
  });

  it.each(['gpt-5.1', 'gpt-5-codex', 'codex-mini', 'o3', 'o4-mini', 'openai/gpt-5'])(
    'colours %s as Codex',
    (model) => {
      expect(engineOf(model)).toBe('codex');
      expect(engineInk(model)).toBe('var(--color-harness-codex)');
    },
  );

  it.each(['qwen3.8-27b', 'grok-4', 'nemotron', 'ollama/llama3', undefined, ''])(
    'uses the brand colour for %s',
    (model) => {
      expect(engineOf(model)).toBe('other');
      expect(engineInk(model)).toBe('var(--color-brand)');
    },
  );

  it('does not read an "o" inside another word as the o-series', () => {
    expect(engineOf('ollama')).toBe('other');
    expect(engineOf('openchat-3')).toBe('other');
  });
});
