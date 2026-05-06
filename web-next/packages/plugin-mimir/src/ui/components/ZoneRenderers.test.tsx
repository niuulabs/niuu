import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { fireEvent } from '@testing-library/react';
import { ZoneBodyReadonly, zoneToEditableText } from './ZoneRenderers';
import type { Zone } from '../../domain/page';
import type { PageMeta } from '../../domain/page';

const MOCK_PAGES: PageMeta[] = [
  {
    path: '/arch/overview',
    title: 'Architecture Overview',
    summary: 'summary',
    category: 'arch',
    type: 'topic',
    confidence: 'high',
    mounts: ['local'],
    updatedAt: '2026-04-18T10:00:00Z',
    updatedBy: 'ravn-fjolnir',
    sourceIds: [],
    size: 100,
  },
];

const noop = () => undefined;

// ── ZoneBodyReadonly — key-facts ──────────────────────────────────────────────

describe('ZoneBodyReadonly — key-facts', () => {
  it('renders each fact as a list item', () => {
    const zone: Zone = { kind: 'key-facts', items: ['Fact one', 'Fact two'] };
    render(<ZoneBodyReadonly zone={zone} allPages={MOCK_PAGES} onNavigate={noop} />);
    expect(screen.getByText('Fact one')).toBeInTheDocument();
    expect(screen.getByText('Fact two')).toBeInTheDocument();
  });

  it('renders resolved wikilinks as clickable pills', () => {
    const zone: Zone = { kind: 'key-facts', items: ['See [[arch/overview]]'] };
    const onNavigate = vi.fn();
    render(<ZoneBodyReadonly zone={zone} allPages={MOCK_PAGES} onNavigate={onNavigate} />);
    const pill = screen.getByRole('button', { name: /navigate to arch\/overview/i });
    expect(pill).toBeInTheDocument();
    fireEvent.click(pill);
    expect(onNavigate).toHaveBeenCalledWith('arch/overview');
  });

  it('renders broken wikilinks with strikethrough and no click handler', () => {
    const zone: Zone = { kind: 'key-facts', items: ['See [[missing/page]]'] };
    render(<ZoneBodyReadonly zone={zone} allPages={MOCK_PAGES} onNavigate={noop} />);
    const pill = screen.getByLabelText(/broken link: missing\/page/i);
    expect(pill).toBeInTheDocument();
  });
});

// ── ZoneBodyReadonly — relationships ─────────────────────────────────────────

describe('ZoneBodyReadonly — relationships', () => {
  it('renders relationship items with slugs and notes', () => {
    const zone: Zone = {
      kind: 'relationships',
      items: [{ slug: 'arch/overview', note: 'parent system' }],
    };
    render(<ZoneBodyReadonly zone={zone} allPages={MOCK_PAGES} onNavigate={noop} />);
    expect(screen.getByRole('button', { name: /navigate to arch\/overview/i })).toBeInTheDocument();
    expect(screen.getByText(/parent system/)).toBeInTheDocument();
  });

  it('marks broken relationship slugs', () => {
    const zone: Zone = {
      kind: 'relationships',
      items: [{ slug: 'does/not/exist', note: 'gone' }],
    };
    render(<ZoneBodyReadonly zone={zone} allPages={MOCK_PAGES} onNavigate={noop} />);
    expect(screen.getByLabelText(/broken link: does\/not\/exist/i)).toBeInTheDocument();
  });
});

// ── ZoneBodyReadonly — assessment ─────────────────────────────────────────────

describe('ZoneBodyReadonly — assessment', () => {
  it('renders the assessment text', () => {
    const zone: Zone = { kind: 'assessment', text: 'Architecture looks solid.' };
    render(<ZoneBodyReadonly zone={zone} allPages={MOCK_PAGES} onNavigate={noop} />);
    expect(screen.getByText('Architecture looks solid.')).toBeInTheDocument();
  });
});

// ── ZoneBodyReadonly — timeline ───────────────────────────────────────────────

describe('ZoneBodyReadonly — timeline', () => {
  it('renders timeline entries', () => {
    const zone: Zone = {
      kind: 'timeline',
      items: [
        { date: '2026-01-10', note: 'First entry', source: 'src-1' },
        { date: '2026-03-20', note: 'Second entry', source: 'src-2' },
      ],
    };
    render(<ZoneBodyReadonly zone={zone} allPages={MOCK_PAGES} onNavigate={noop} />);
    expect(screen.getByText('2026-01-10')).toBeInTheDocument();
    expect(screen.getByText('First entry')).toBeInTheDocument();
    expect(screen.getByText('2026-03-20')).toBeInTheDocument();
    expect(screen.getByText('Second entry')).toBeInTheDocument();
  });

  it('renders newest entries first', () => {
    const zone: Zone = {
      kind: 'timeline',
      items: [
        { date: '2026-01-10', note: 'older', source: 'src-1' },
        { date: '2026-03-20', note: 'newer', source: 'src-2' },
      ],
    };
    render(<ZoneBodyReadonly zone={zone} allPages={MOCK_PAGES} onNavigate={noop} />);
    const dates = screen.getAllByText(/2026-\d\d-\d\d/);
    // Newest first: 2026-03-20 before 2026-01-10
    expect(dates[0]?.textContent).toBe('2026-03-20');
    expect(dates[1]?.textContent).toBe('2026-01-10');
  });

  it('renders empty state when there are no entries', () => {
    const zone: Zone = { kind: 'timeline', items: [] };
    render(<ZoneBodyReadonly zone={zone} allPages={MOCK_PAGES} onNavigate={noop} />);
    expect(screen.getByText('No timeline entries yet')).toBeInTheDocument();
  });

  it('does not mutate the original items array when sorting', () => {
    const items = [
      { date: '2026-01-10', note: 'older', source: 'src-1' },
      { date: '2026-03-20', note: 'newer', source: 'src-2' },
    ];
    const zone: Zone = { kind: 'timeline', items };
    render(<ZoneBodyReadonly zone={zone} allPages={MOCK_PAGES} onNavigate={noop} />);
    // Original array order must be preserved
    expect(items[0]?.date).toBe('2026-01-10');
    expect(items[1]?.date).toBe('2026-03-20');
  });
});

// ── zoneToEditableText ────────────────────────────────────────────────────────

describe('zoneToEditableText', () => {
  it('converts key-facts to newline-separated strings', () => {
    const zone: Zone = { kind: 'key-facts', items: ['a', 'b'] };
    expect(zoneToEditableText(zone)).toBe('a\nb');
  });

  it('converts relationships to [[slug]] — note format', () => {
    const zone: Zone = {
      kind: 'relationships',
      items: [{ slug: 'foo', note: 'bar' }],
    };
    expect(zoneToEditableText(zone)).toBe('[[foo]] — bar');
  });

  it('converts assessment to plain text', () => {
    const zone: Zone = { kind: 'assessment', text: 'some assessment' };
    expect(zoneToEditableText(zone)).toBe('some assessment');
  });

  it('converts timeline to date: note format', () => {
    const zone: Zone = {
      kind: 'timeline',
      items: [{ date: '2026-01-10', note: 'event', source: 'src' }],
    };
    expect(zoneToEditableText(zone)).toBe('2026-01-10: event');
  });
});
