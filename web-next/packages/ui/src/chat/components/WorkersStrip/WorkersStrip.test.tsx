import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { WorkersStrip, workerInitial } from './WorkersStrip';
import { SessionLinksProvider, type SessionLink } from '../../sessionLinks';
import type { Worker } from '../../workers';

const worker = (peerId: string, name: string, status: Worker['status'], color: string): Worker => ({
  peerId,
  name,
  persona: name,
  color,
  status,
  consumes: [],
  emits: [],
  outcomes: [],
});

const workers = [
  worker('c', 'coder', 'working', '#111111'),
  worker('r', 'reviewer', 'idle', '#222222'),
  worker('s', 'security-auditor', 'error', '#333333'),
];

describe('WorkersStrip', () => {
  it('labels the strip Workers and shows one chip per worker', () => {
    render(<WorkersStrip workers={workers} onSelect={vi.fn()} />);
    const strip = screen.getByTestId('workers-strip');
    expect(within(strip).getByText('Workers')).toBeInTheDocument();
    expect(
      within(strip)
        .getAllByRole('button')
        .map((chip) => chip.textContent),
    ).toEqual(['Ccoder', 'Rreviewer', 'Ssecurity-auditor']);
  });

  it('colours each chip with the worker colour and an initial avatar', () => {
    render(<WorkersStrip workers={workers} onSelect={vi.fn()} />);
    const chip = screen.getByRole('button', { name: /reviewer/ });
    expect(chip.style.getPropertyValue('--niuu-worker-color')).toBe('#222222');
    expect(chip.querySelector('.niuu-chat-worker-avatar')).toHaveTextContent('R');
    expect(workerInitial(workers[2]!)).toBe('S');
  });

  it('shows working, idle and error as distinct status dots', () => {
    render(<WorkersStrip workers={workers} onSelect={vi.fn()} />);
    expect(screen.getByRole('img', { name: 'Working' })).toHaveAttribute('data-status', 'working');
    expect(screen.getByRole('img', { name: 'Idle' })).toHaveAttribute('data-status', 'idle');
    expect(screen.getByRole('img', { name: 'Error' })).toHaveAttribute('data-status', 'error');
  });

  it('opens a worker when its chip is clicked', () => {
    const onSelect = vi.fn();
    render(<WorkersStrip workers={workers} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole('button', { name: /coder/ }));
    expect(onSelect).toHaveBeenCalledWith('c');
  });

  it('marks the worker whose sheet is open', () => {
    render(<WorkersStrip workers={workers} activePeerId="r" onSelect={vi.fn()} />);
    expect(screen.getByRole('button', { name: /reviewer/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: /coder/ })).toHaveAttribute('aria-pressed', 'false');
  });

  it('lists worker sessions first, each a link that opens its session', () => {
    const open = vi.fn();
    const sessions: SessionLink[] = [
      {
        id: 'w1',
        name: 'ticket-1342-gates',
        stateLabel: 'Needs you',
        tone: 'warn',
        href: '/volundr/sessions/w1',
      },
    ];
    render(
      <SessionLinksProvider value={{ resolve: () => null, open }}>
        <WorkersStrip workers={workers} sessions={sessions} onSelect={vi.fn()} />
      </SessionLinksProvider>,
    );
    const link = screen.getByRole('link', { name: 'Open worker ticket-1342-gates, Needs you' });
    expect(link).toHaveAttribute('href', '/volundr/sessions/w1');
    expect(link).toHaveTextContent('Needs you');
    expect(link.querySelector('.niuu-chat-worker-status--waiting')).not.toBeNull();
    fireEvent.click(link);
    expect(open).toHaveBeenCalledWith('w1');
  });

  it('shows no session chips without session links to open them', () => {
    render(
      <WorkersStrip
        workers={workers}
        sessions={[{ id: 'w1', name: 'w', stateLabel: 'Idle', tone: 'muted', href: '/w1' }]}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.queryByRole('link')).toBeNull();
  });
});
