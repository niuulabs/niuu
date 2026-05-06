import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ZoneBlock } from './ZoneBlock';
import type { Zone } from '../../domain/page';
import type { ZoneEditState } from '../../domain/zone-edit';

const MOCK_PAGES: [] = [];
const IDLE: ZoneEditState = { status: 'idle' };
const noop = () => undefined;
const asyncNoop = async () => undefined;

function renderZone(zone: Zone, editState: ZoneEditState = IDLE) {
  return render(
    <ZoneBlock
      zone={zone}
      pagePath="/test/page"
      pageMounts={['local']}
      allPages={MOCK_PAGES}
      onNavigate={noop}
      editState={editState}
      onEdit={noop}
      onSave={asyncNoop}
      onCancel={noop}
    />,
  );
}

// ── Label rendering ───────────────────────────────────────────────────────────

describe('ZoneBlock labels', () => {
  it('shows "Key facts" label for key-facts zone', () => {
    renderZone({ kind: 'key-facts', items: [] });
    expect(screen.getByText('Key facts')).toBeInTheDocument();
  });

  it('shows "Timeline" label for timeline zone', () => {
    renderZone({ kind: 'timeline', items: [] });
    expect(screen.getByText('Timeline')).toBeInTheDocument();
  });

  it('shows "Assessment" label for assessment zone', () => {
    renderZone({ kind: 'assessment', text: '' });
    expect(screen.getByText('Assessment')).toBeInTheDocument();
  });

  it('shows "Relationships" label for relationships zone', () => {
    renderZone({ kind: 'relationships', items: [] });
    expect(screen.getByText('Relationships')).toBeInTheDocument();
  });
});

// ── Edit button suppression ───────────────────────────────────────────────────

describe('ZoneBlock edit button', () => {
  it('shows edit button for key-facts zone when idle', () => {
    renderZone({ kind: 'key-facts', items: [] });
    expect(screen.getByRole('button', { name: /edit key-facts zone/i })).toBeInTheDocument();
  });

  it('shows edit button for assessment zone when idle', () => {
    renderZone({ kind: 'assessment', text: 'text' });
    expect(screen.getByRole('button', { name: /edit assessment zone/i })).toBeInTheDocument();
  });

  it('shows edit button for relationships zone when idle', () => {
    renderZone({ kind: 'relationships', items: [] });
    expect(screen.getByRole('button', { name: /edit relationships zone/i })).toBeInTheDocument();
  });

  it('does NOT show edit button for timeline zone', () => {
    renderZone({ kind: 'timeline', items: [] });
    expect(screen.queryByRole('button', { name: /edit timeline zone/i })).toBeNull();
  });

  it('calls onEdit when edit button is clicked', () => {
    const onEdit = vi.fn();
    const zone: Zone = { kind: 'key-facts', items: ['fact'] };
    render(
      <ZoneBlock
        zone={zone}
        pagePath="/test/page"
        pageMounts={['local']}
        allPages={MOCK_PAGES}
        onNavigate={noop}
        editState={IDLE}
        onEdit={onEdit}
        onSave={asyncNoop}
        onCancel={noop}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /edit key-facts zone/i }));
    expect(onEdit).toHaveBeenCalledWith(zone);
  });
});

// ── Editing state ─────────────────────────────────────────────────────────────

describe('ZoneBlock editing state', () => {
  const editingState: ZoneEditState = {
    status: 'editing',
    path: '/test/page',
    zoneKind: 'key-facts',
    draft: { kind: 'key-facts', items: ['existing fact'] },
  };

  it('shows save and cancel buttons when editing', () => {
    renderZone({ kind: 'key-facts', items: ['existing fact'] }, editingState);
    expect(screen.getByRole('button', { name: /save key-facts zone/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /cancel edit/i })).toBeInTheDocument();
  });

  it('shows textarea with editable text when editing', () => {
    renderZone({ kind: 'key-facts', items: ['existing fact'] }, editingState);
    expect(screen.getByRole('textbox', { name: /zone edit area/i })).toBeInTheDocument();
  });

  it('calls onCancel when cancel is clicked', () => {
    const onCancel = vi.fn();
    render(
      <ZoneBlock
        zone={{ kind: 'key-facts', items: ['fact'] }}
        pagePath="/test/page"
        pageMounts={['local']}
        allPages={MOCK_PAGES}
        onNavigate={noop}
        editState={editingState}
        onEdit={noop}
        onSave={asyncNoop}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /cancel edit/i }));
    expect(onCancel).toHaveBeenCalled();
  });

  it('calls onSave with textarea value when save is clicked', () => {
    const onSave = vi.fn();
    render(
      <ZoneBlock
        zone={{ kind: 'key-facts', items: ['fact'] }}
        pagePath="/test/page"
        pageMounts={['local']}
        allPages={MOCK_PAGES}
        onNavigate={noop}
        editState={editingState}
        onEdit={noop}
        onSave={onSave}
        onCancel={noop}
      />,
    );
    const textarea = screen.getByRole('textbox', { name: /zone edit area/i });
    fireEvent.change(textarea, { target: { value: 'updated fact' } });
    fireEvent.click(screen.getByRole('button', { name: /save key-facts zone/i }));
    expect(onSave).toHaveBeenCalledWith('updated fact');
  });
});

// ── Save / error banners ──────────────────────────────────────────────────────

describe('ZoneBlock banners', () => {
  it('shows save banner after successful save', () => {
    const savedState: ZoneEditState = {
      status: 'saved',
      path: '/test/page',
      savedAt: '2026-04-18T10:00:00Z',
    };
    renderZone({ kind: 'key-facts', items: [] }, savedState);
    expect(screen.getByText(/saved/i)).toBeInTheDocument();
  });

  it('shows error banner on save error', () => {
    const errorState: ZoneEditState = {
      status: 'error',
      path: '/test/page',
      message: 'network timeout',
    };
    renderZone({ kind: 'key-facts', items: [] }, errorState);
    expect(screen.getByText('network timeout')).toBeInTheDocument();
  });
});
