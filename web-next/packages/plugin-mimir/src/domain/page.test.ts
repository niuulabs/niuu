import { describe, it, expect } from 'vitest';
import { isHighConfidence, getZoneByKind, toPageMeta } from './page';
import type { Page, Zone, ZoneKeyFacts, ZoneAssessment } from './page';

const basePage: Page = {
  path: '/arch/overview',
  title: 'Architecture Overview',
  summary: 'High-level view of the Niuu platform architecture.',
  category: 'arch',
  type: 'topic',
  confidence: 'high',
  mounts: ['local'],
  updatedAt: '2026-04-18T10:00:00Z',
  updatedBy: 'ravn-fjolnir',
  sourceIds: ['src-001'],
  related: ['/arch/hexagonal'],
  size: 3200,
};

describe('isHighConfidence', () => {
  it('returns true for high confidence', () => {
    expect(isHighConfidence({ confidence: 'high' })).toBe(true);
  });

  it('returns false for medium confidence', () => {
    expect(isHighConfidence({ confidence: 'medium' })).toBe(false);
  });

  it('returns false for low confidence', () => {
    expect(isHighConfidence({ confidence: 'low' })).toBe(false);
  });
});

describe('getZoneByKind', () => {
  const keyFacts: ZoneKeyFacts = { kind: 'key-facts', items: ['fact one', 'fact two'] };
  const assessment: ZoneAssessment = { kind: 'assessment', text: 'Looking good.' };
  const zones: Zone[] = [keyFacts, assessment];

  it('finds a zone by its kind', () => {
    const result = getZoneByKind(zones, 'key-facts');
    expect(result).toBeDefined();
    expect(result!.kind).toBe('key-facts');
  });

  it('returns the typed zone', () => {
    const result = getZoneByKind(zones, 'key-facts');
    expect(result!.items).toEqual(['fact one', 'fact two']);
  });

  it('returns undefined when the kind is not present', () => {
    const result = getZoneByKind(zones, 'timeline');
    expect(result).toBeUndefined();
  });

  it('returns undefined for empty zones array', () => {
    expect(getZoneByKind([], 'assessment')).toBeUndefined();
  });
});

describe('toPageMeta', () => {
  it('carries related through and strips zones from a Page', () => {
    const page: Page = {
      ...basePage,
      related: ['/other'],
      zones: [{ kind: 'assessment', text: 'ok' }],
    };
    const meta = toPageMeta(page);
    expect(meta.related).toEqual(['/other']);
    expect(meta).not.toHaveProperty('zones');
  });

  it('preserves all PageMeta fields', () => {
    const meta = toPageMeta(basePage);
    expect(meta.path).toBe('/arch/overview');
    expect(meta.title).toBe('Architecture Overview');
    expect(meta.type).toBe('topic');
    expect(meta.confidence).toBe('high');
    expect(meta.mounts).toEqual(['local']);
    expect(meta.updatedBy).toBe('ravn-fjolnir');
    expect(meta.sourceIds).toEqual(['src-001']);
    expect(meta.size).toBe(3200);
  });
});

describe('Zone discriminated union', () => {
  it('key-facts zone has items array', () => {
    const zone: Zone = { kind: 'key-facts', items: ['a', 'b'] };
    expect(zone.kind).toBe('key-facts');
    if (zone.kind === 'key-facts') {
      expect(zone.items).toHaveLength(2);
    }
  });

  it('relationships zone has items with slug and note', () => {
    const zone: Zone = {
      kind: 'relationships',
      items: [{ slug: '/other', note: 'related concept' }],
    };
    expect(zone.kind).toBe('relationships');
    if (zone.kind === 'relationships') {
      expect(zone.items[0]!.slug).toBe('/other');
    }
  });

  it('assessment zone has text', () => {
    const zone: Zone = { kind: 'assessment', text: 'Solid approach.' };
    expect(zone.kind).toBe('assessment');
    if (zone.kind === 'assessment') {
      expect(zone.text).toBe('Solid approach.');
    }
  });

  it('timeline zone has items with date, note, source', () => {
    const zone: Zone = {
      kind: 'timeline',
      items: [{ date: '2026-01-01', note: 'Initial commit', source: 'src-001' }],
    };
    expect(zone.kind).toBe('timeline');
    if (zone.kind === 'timeline') {
      expect(zone.items[0]!.date).toBe('2026-01-01');
    }
  });
});
