import { describe, expect, it } from 'vitest';
import { trustSentence } from './trust';

describe('trustSentence', () => {
  it('reads the rungs back as one sentence', () => {
    expect(trustSentence({ draft: 2, build: 2, test: 2, deploy: 1, spend: 1 })).toBe(
      'Draft, build and test on its own · asks before deploy and spend · never mutate',
    );
    expect(trustSentence({ build: 2 })).toBe(
      'Build on its own · never draft, test, deploy, mutate and spend',
    );
    expect(trustSentence({})).toBe('Never draft, build, test, deploy, mutate and spend');
  });
});
