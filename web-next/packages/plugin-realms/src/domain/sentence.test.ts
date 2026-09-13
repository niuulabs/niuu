import { describe, expect, it } from 'vitest';
import { parseSentence } from './sentence';

describe('parseSentence', () => {
  it('reads the repository, the board and the ask-before rules from one sentence', () => {
    const draft = parseSentence(
      'Keep niuulabs/lexi-api shippable, work the LXA board in priority order, and ask me before anything deploys.',
    );
    expect(draft.repo).toBe('niuulabs/lexi-api');
    expect(draft.boardKey).toBe('LXA');
    expect(draft.askBefore).toEqual(['deploy']);
    expect(draft.name).toBe('lexi api');
    expect(draft.charter).toContain('Keep niuulabs/lexi-api shippable');
  });

  it('finds a board written as "board XYZ" and several ask-before rules', () => {
    const draft = parseSentence(
      'Work board NIU for acme/storefront. Ask before merging, ask before you spend, never touch production.',
    );
    expect(draft.boardKey).toBe('NIU');
    expect(draft.repo).toBe('acme/storefront');
    expect(draft.askBefore).toEqual(['build', 'spend', 'mutate']);
  });

  it('leaves the blanks null when the sentence has none of them', () => {
    const draft = parseSentence('Keep the lab tidy and tell me when the printer jams.');
    expect(draft.repo).toBeNull();
    expect(draft.boardKey).toBeNull();
    expect(draft.name).toBeNull();
    expect(draft.askBefore).toEqual([]);
    expect(draft.charter).toBe('Keep the lab tidy and tell me when the printer jams.');
  });

  it('strips a trailing .git from the repository', () => {
    expect(parseSentence('Watch niuulabs/volundr.git').repo).toBe('niuulabs/volundr');
  });
});
