import { expect, it } from 'vitest';
import { sessionModelLabel } from './sessionModelLabel';
it.each([
  ['gpt-6-astra', 'GPT-6 Astra'],
  ['gpt-5.6-sol', 'GPT-5.6 Sol'],
  ['gpt-6-sol', 'GPT-6 Sol'],
  ['gpt-6-luna', 'GPT-6 Luna'],
  ['gpt-6', 'GPT-6'],
  ['claude-opus-5', 'Claude Opus 5'],
  ['claude-opus-5-5', 'Claude Opus 5.5'],
  ['claude-fable-5-1', 'Claude Fable 5.1'],
  ['claude-sonnet-4-6-20260217', 'Claude Sonnet 4.6'],
  ['custom/model-v2', 'custom/model-v2'],
])('formats %s without guessing a version', (id, label) =>
  expect(sessionModelLabel(id)).toBe(label),
);
