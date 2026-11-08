import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { AttentionRow } from './AttentionRow';
import type { MeshNotificationEvent } from '../../types';

const event: MeshNotificationEvent = {
  id: 'n1',
  type: 'notification',
  timestamp: new Date('2026-11-05T05:30:00Z'),
  participantId: 'coder',
  participant: {},
  persona: 'coder',
  notificationType: 'help_needed',
  summary: 'Two retired models are still pinned.',
  urgency: 0.8,
};

describe('AttentionRow', () => {
  it('shows who raised it, what, and its kind on one line', () => {
    render(<AttentionRow event={event} onOpenWorker={vi.fn()} />);
    const row = screen.getByTestId('attention-row');
    expect(row).toHaveTextContent('coder');
    expect(row).toHaveTextContent('Two retired models are still pinned.');
    expect(row).toHaveTextContent('Help needed');
  });

  it('opens the worker it came from', () => {
    const onOpenWorker = vi.fn();
    render(
      <AttentionRow
        event={event}
        worker={{ peerId: 'coder', name: 'coder-1', color: '#123456' }}
        onOpenWorker={onOpenWorker}
      />,
    );
    fireEvent.click(screen.getByRole('button'));
    expect(onOpenWorker).toHaveBeenCalledWith('coder');
    expect(screen.getByText('coder-1')).toBeInTheDocument();
  });

  it('is plain text when the room does not list the worker', () => {
    render(<AttentionRow event={event} onOpenWorker={vi.fn()} />);
    expect(screen.queryByRole('button')).toBeNull();
  });
});
