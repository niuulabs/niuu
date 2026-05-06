import { describe, it, expect } from 'vitest';
import {
  splitWikilinks,
  parseWikilinks,
  resolveWikilink,
  resolveAll,
  detectBrokenWikilinks,
} from './wikilink';
import type { PageMeta } from './page';

const makePage = (path: string): PageMeta => ({
  path,
  title: path,
  summary: '',
  category: 'test',
  type: 'topic',
  confidence: 'high',
  mounts: ['local'],
  updatedAt: '2026-01-01T00:00:00Z',
  updatedBy: 'ravn-test',
  sourceIds: [],
  size: 100,
});

const PAGES: PageMeta[] = [
  makePage('/arch/overview'),
  makePage('/api/auth'),
  makePage('/infra/k8s'),
];

describe('splitWikilinks', () => {
  it('returns a single text part when no wikilinks present', () => {
    expect(splitWikilinks('plain text')).toEqual([{ kind: 'text', value: 'plain text' }]);
  });

  it('returns empty array for empty string', () => {
    expect(splitWikilinks('')).toEqual([]);
  });

  it('splits a single wikilink with surrounding text', () => {
    expect(splitWikilinks('see [[arch/overview]] for more')).toEqual([
      { kind: 'text', value: 'see ' },
      { kind: 'link', slug: 'arch/overview' },
      { kind: 'text', value: ' for more' },
    ]);
  });

  it('handles a wikilink at the start of the string', () => {
    expect(splitWikilinks('[[arch/overview]] and more')).toEqual([
      { kind: 'link', slug: 'arch/overview' },
      { kind: 'text', value: ' and more' },
    ]);
  });

  it('handles a wikilink at the end of the string', () => {
    expect(splitWikilinks('see [[arch/overview]]')).toEqual([
      { kind: 'text', value: 'see ' },
      { kind: 'link', slug: 'arch/overview' },
    ]);
  });

  it('handles multiple wikilinks', () => {
    expect(splitWikilinks('[[a/b]] and [[c/d]]')).toEqual([
      { kind: 'link', slug: 'a/b' },
      { kind: 'text', value: ' and ' },
      { kind: 'link', slug: 'c/d' },
    ]);
  });

  it('trims whitespace inside brackets', () => {
    expect(splitWikilinks('[[ arch/overview ]]')).toEqual([
      { kind: 'link', slug: 'arch/overview' },
    ]);
  });
});

describe('parseWikilinks', () => {
  it('returns empty array when no wikilinks present', () => {
    expect(parseWikilinks('plain text with no links')).toEqual([]);
  });

  it('extracts a single [[slug]]', () => {
    expect(parseWikilinks('see [[arch/overview]] for context')).toEqual(['arch/overview']);
  });

  it('extracts multiple wikilinks', () => {
    expect(parseWikilinks('[[arch/overview]] and [[api/auth]]')).toEqual([
      'arch/overview',
      'api/auth',
    ]);
  });

  it('trims whitespace inside the brackets', () => {
    expect(parseWikilinks('[[ arch/overview ]]')).toEqual(['arch/overview']);
  });

  it('ignores unclosed brackets', () => {
    expect(parseWikilinks('[[unclosed')).toEqual([]);
  });
});

describe('resolveWikilink', () => {
  it('resolves to the matching page', () => {
    const result = resolveWikilink('arch/overview', PAGES);
    expect(result.broken).toBe(false);
    expect(result.page?.path).toBe('/arch/overview');
  });

  it('returns broken=true when no page matches', () => {
    const result = resolveWikilink('does-not-exist', PAGES);
    expect(result.broken).toBe(true);
    expect(result.page).toBeNull();
  });

  it('matches by substring of page path', () => {
    const result = resolveWikilink('api/auth', PAGES);
    expect(result.broken).toBe(false);
  });
});

describe('resolveAll', () => {
  it('resolves an array of slugs', () => {
    const results = resolveAll(['arch/overview', 'missing-slug'], PAGES);
    expect(results).toHaveLength(2);
    expect(results[0]?.broken).toBe(false);
    expect(results[1]?.broken).toBe(true);
  });
});

describe('detectBrokenWikilinks', () => {
  it('returns only broken links from text', () => {
    const text = '[[arch/overview]] is fine but [[ghost-page]] is broken';
    const broken = detectBrokenWikilinks(text, PAGES);
    expect(broken).toHaveLength(1);
    expect(broken[0]?.slug).toBe('ghost-page');
  });

  it('returns empty array when all links resolve', () => {
    const text = '[[arch/overview]] and [[api/auth]]';
    expect(detectBrokenWikilinks(text, PAGES)).toHaveLength(0);
  });

  it('returns empty array for text with no wikilinks', () => {
    expect(detectBrokenWikilinks('plain text', PAGES)).toHaveLength(0);
  });
});
