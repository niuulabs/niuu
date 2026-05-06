import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { buildSessionColumns } from './sessionColumns';
import type { Session } from '../../domain/session';

const SESSION: Session = {
  id: 'sess-123',
  state: 'running',
  personaName: 'Review Ravn',
  clusterId: 'cl-eitri',
  startedAt: '2026-05-01T12:00:00.000Z',
  taskSummary: 'Review coverage',
};

describe('buildSessionColumns', () => {
  it('builds the requested columns and renders session values', () => {
    const onView = vi.fn();
    const columns = buildSessionColumns({ onView });

    expect(columns.map((column) => column.key)).toEqual([
      'id',
      'persona',
      'cluster',
      'state',
      'started',
      'actions',
    ]);

    render(
      <div>
        {columns.map((column) => (
          <div key={column.key}>{column.render?.(SESSION)}</div>
        ))}
      </div>,
    );

    expect(screen.getByText('sess-123')).toBeInTheDocument();
    expect(screen.getByText('Review Ravn')).toBeInTheDocument();
    expect(screen.getByText('cl-eitri')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'View session sess-123' })).toBeInTheDocument();
  });

  it('uses custom button labels and selected column order', () => {
    const onView = vi.fn();
    const columns = buildSessionColumns({
      onView,
      buttonLabel: 'Open →',
      testIdPrefix: 'open-session',
      columns: ['actions', 'id'],
    });

    render(
      <div>
        {columns.map((column) => (
          <div key={column.key}>{column.render?.(SESSION)}</div>
        ))}
      </div>,
    );

    fireEvent.click(screen.getByTestId('open-session-sess-123'));
    expect(onView).toHaveBeenCalledWith('sess-123');
    expect(screen.getByRole('button', { name: 'Open session sess-123' })).toHaveTextContent(
      'Open →',
    );
  });
});
