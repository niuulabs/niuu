import { describe, expect, it } from 'vitest';
import {
  BALANCED_TRUST,
  EMPTY_DRAFT,
  charterPagePathFor,
  deploymentNameFor,
  isValidSlug,
  mountNameFor,
  personaNameFor,
  requiredDraftError,
  residentNameFor,
  routingPrefixFor,
  routingRuleIdFor,
  slugify,
  trustSettingForLevel,
  type RealmDraft,
} from './realm';

describe('realm naming conventions', () => {
  it('derives every part of a realm from its slug', () => {
    expect(personaNameFor('lexi-api')).toBe('realm-lexi-api');
    expect(residentNameFor('lexi-api')).toBe('lexi-api');
    expect(deploymentNameFor('lexi-api')).toBe('realm-lexi-api');
    expect(mountNameFor('lexi-api')).toBe('realm-lexi-api');
    expect(routingRuleIdFor('lexi-api')).toBe('realm-lexi-api');
    expect(routingPrefixFor('lexi-api')).toBe('realms/lexi-api/');
    expect(charterPagePathFor('lexi-api')).toBe('realms/lexi-api/charter.md');
  });

  it('slugifies names the way the realm API expects', () => {
    expect(slugify('Lexi API')).toBe('lexi-api');
    expect(slugify('  Volundr platform!! ')).toBe('volundr-platform');
    expect(isValidSlug('lexi-api')).toBe(true);
    expect(isValidSlug('-bad')).toBe(false);
    expect(isValidSlug('Bad')).toBe(false);
  });

  it('maps grant levels onto the three-way trust setting', () => {
    expect(trustSettingForLevel(null)).toBe('never');
    expect(trustSettingForLevel(undefined)).toBe('never');
    expect(trustSettingForLevel(1)).toBe('ask');
    expect(trustSettingForLevel(2)).toBe('auto');
    expect(trustSettingForLevel(5)).toBe('auto');
  });
});

describe('requiredDraftError', () => {
  const complete: RealmDraft = {
    ...EMPTY_DRAFT,
    slug: 'lexi-api',
    name: 'Lexi API',
    charter: 'Keep it shippable.',
    repo: 'niuulabs/lexi-api',
    trackerBoard: 'board-1',
    profileId: 'profile-1',
    instanceId: 'instance-1',
    mountTarget: 'ymir',
    trust: BALANCED_TRUST,
  };

  it('accepts a complete draft', () => {
    expect(requiredDraftError(complete, true)).toBeNull();
  });

  it('names the first missing thing', () => {
    expect(requiredDraftError({ ...complete, slug: '' }, true)).toMatch(/lowercase/);
    expect(requiredDraftError({ ...complete, name: ' ' }, true)).toMatch(/name/);
    expect(requiredDraftError({ ...complete, charter: '' }, true)).toMatch(/charter/);
    expect(requiredDraftError({ ...complete, repo: '' }, true)).toMatch(/repository/);
    expect(requiredDraftError({ ...complete, trackerBoard: '' }, true)).toMatch(/board/);
    expect(requiredDraftError({ ...complete, trackerBoard: '' }, false)).toBeNull();
    expect(requiredDraftError({ ...complete, profileId: '' }, true)).toMatch(/runs/);
    expect(requiredDraftError({ ...complete, mountTarget: '' }, true)).toMatch(/memory/);
  });
});
