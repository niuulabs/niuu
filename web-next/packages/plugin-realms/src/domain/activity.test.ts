import { describe, expect, it } from 'vitest';
import { decisionLine, decisionTone, trustSentence } from './activity';

describe('decisionLine', () => {
  it('drops the resident and environment prefix and capitalises the verb phrase', () => {
    expect(
      decisionLine({
        summary:
          'Valkyrie valkyrie-valhalla-k8s in valhalla recommends: continue monitoring (present)',
        recommendedAction: '',
      }),
    ).toBe('Continue monitoring (present)');
    expect(
      decisionLine({
        summary: 'Valkyrie v in valhalla judged nothing needs action',
        recommendedAction: '',
      }),
    ).toBe('Nothing needs action');
  });

  it('falls back to the recommended action and keeps text without a prefix', () => {
    expect(decisionLine({ summary: '', recommendedAction: 'restart the pod' })).toBe(
      'Restart the pod',
    );
    expect(decisionLine({ summary: 'Valkyrie x in y recommends: ', recommendedAction: '' })).toBe(
      'Valkyrie x in y recommends:',
    );
  });
});

describe('decisionTone', () => {
  it('maps outcomes to a dot tone', () => {
    expect(decisionTone({ outcome: 'failed', actionAuthority: 'autonomous' })).toBe('warn');
    expect(decisionTone({ outcome: 'pending_review', actionAuthority: 'guarded' })).toBe('warn');
    expect(decisionTone({ outcome: 'observation_only', actionAuthority: 'autonomous' })).toBe(
      'muted',
    );
    expect(decisionTone({ outcome: 'acted', actionAuthority: 'autonomous' })).toBe('ok');
    expect(decisionTone({ outcome: 'acted', actionAuthority: 'guarded' })).toBe('brand');
  });
});

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
