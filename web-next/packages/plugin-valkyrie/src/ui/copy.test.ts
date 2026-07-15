import { describe, expect, it } from 'vitest';
import {
  actionAuthorityCopy,
  autonomyModeCopy,
  decisionStatusCopy,
  describeIdleSituation,
  isLearnedSkillState,
  operationalStateCopy,
  outcomeCopy,
  reviewKindLabel,
  severityCopy,
  wakefulnessCopy,
} from './copy';

describe('operationalStateCopy', () => {
  it('translates internal states into human labels', () => {
    expect(operationalStateCopy('watching').label).toBe('Watching');
    expect(operationalStateCopy('using_adopted_learning').label).toBe(
      'Handled with a learned skill',
    );
    expect(operationalStateCopy('adopted_learning_regressed').label).toBe(
      'Learned skill rolled back',
    );
  });

  it('never leaks snake_case for unknown states', () => {
    const copy = operationalStateCopy('custom_new_state');
    expect(copy.label).toBe('Custom new state');
    expect(copy.label).not.toContain('_');
  });

  it('handles empty values without crashing', () => {
    expect(operationalStateCopy('').label).toBe('Unknown');
    expect(actionAuthorityCopy('').label).toBe('Unknown');
  });
});

describe('actionAuthorityCopy', () => {
  it('explains what each authority means for the operator', () => {
    expect(actionAuthorityCopy('human_review_required').label).toBe('Needs your approval');
    expect(actionAuthorityCopy('court_required').label).toBe('Needs court approval');
    expect(actionAuthorityCopy('autonomous').description).toContain('no human');
  });
});

describe('decisionStatusCopy', () => {
  it('claims approval only for judgments that truly reach the inbox', () => {
    expect(
      decisionStatusCopy({
        actionAuthority: 'human_review_required',
        tier: 'present',
        recommendedAction: 'restart_deployment',
      }).label,
    ).toBe('Needs your approval');
  });

  it('reads ambient review-authority judgments as observation, not pending', () => {
    expect(
      decisionStatusCopy({
        actionAuthority: 'human_review_required',
        tier: 'ambient',
        recommendedAction: 'restart_deployment',
      }).label,
    ).toBe('Observation only');
  });

  it('reads non-action verdicts as observation regardless of authority', () => {
    expect(
      decisionStatusCopy({
        actionAuthority: 'autonomous',
        tier: 'ambient',
        recommendedAction: 'none',
      }).label,
    ).toBe('Observation only');
    expect(
      decisionStatusCopy({
        actionAuthority: 'human_review_required',
        tier: 'urgent',
        recommendedAction: 'watch',
      }).label,
    ).toBe('Observation only');
  });

  it('keeps the plain authority copy for real autonomous actions', () => {
    expect(
      decisionStatusCopy({
        actionAuthority: 'autonomous',
        tier: 'ambient',
        recommendedAction: 'inspect_with_adopted_learning',
      }).label,
    ).toBe('Acted autonomously');
  });
});

describe('autonomyModeCopy', () => {
  it('describes every selectable mode', () => {
    expect(autonomyModeCopy('guarded').description).toContain('approval');
    expect(autonomyModeCopy('autonomous').description).toContain('review inbox');
    expect(autonomyModeCopy('yolo').description).toContain('rollback');
  });
});

describe('severity and wakefulness copy', () => {
  it('labels known values and falls back cleanly', () => {
    expect(severityCopy('critical').label).toBe('Critical');
    expect(severityCopy('weird').label).toBe('Weird');
    expect(wakefulnessCopy('dreaming').label).toBe('Dreaming');
  });
});

describe('outcomeCopy', () => {
  it('labels action outcomes and returns null when there is none', () => {
    expect(outcomeCopy('executed')?.label).toBe('Action executed');
    expect(outcomeCopy('failed')?.label).toBe('Action failed');
    expect(outcomeCopy('')).toBeNull();
  });
});

describe('reviewKindLabel', () => {
  it('labels all kinds including briefs', () => {
    expect(reviewKindLabel('morning_brief')).toBe('Morning brief');
    expect(reviewKindLabel('court_escalation')).toBe('Action approval');
    expect(reviewKindLabel('unknown_kind')).toBe('Unknown kind');
  });
});

describe('isLearnedSkillState', () => {
  it('recognizes only learned-skill judgment states', () => {
    expect(isLearnedSkillState('using_adopted_learning')).toBe(true);
    expect(isLearnedSkillState('adopted_learning_failed')).toBe(true);
    expect(isLearnedSkillState('watching')).toBe(false);
  });
});

describe('describeIdleSituation', () => {
  it('explains a silent environment', () => {
    expect(describeIdleSituation(0, ['warning'])).toContain('No signals');
  });

  it('explains why observed signals produced no task', () => {
    const text = describeIdleSituation(37, ['warning', 'critical']);
    expect(text).toContain('37 signal(s) observed');
    expect(text).toContain('warning or critical');
  });

  it('falls back to the default thresholds when unconfigured', () => {
    expect(describeIdleSituation(5, undefined)).toContain('warning or critical');
  });
});
