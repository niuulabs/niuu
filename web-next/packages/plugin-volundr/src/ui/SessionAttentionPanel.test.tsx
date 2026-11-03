import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { TrackerIssue } from '../models/volundr.model';
import { SessionAttentionPanel } from './SessionAttentionPanel';

type PanelProps = Parameters<typeof SessionAttentionPanel>[0];

function renderPanel(overrides: Partial<PanelProps> = {}) {
  const props: PanelProps = {
    items: [],
    peers: [],
    checklist: [],
    showOpenChat: false,
    onOpenChat: vi.fn(),
    onOpenDiffs: vi.fn(),
    onRespondPermission: vi.fn(),
    ...overrides,
  };
  render(<SessionAttentionPanel {...props} />);
  return props;
}

const ticket: TrackerIssue = {
  id: 'issue-1',
  identifier: 'NIU-142',
  title: 'Ship workspace invitations',
  status: 'in_progress',
  url: 'https://tracker.example/NIU-142',
};

describe('SessionAttentionPanel', () => {
  it('says plainly when nothing is waiting and hides empty sections', () => {
    renderPanel();

    expect(screen.getByText('Nothing is waiting on you.')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Ticket' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Running' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Changes' })).not.toBeInTheDocument();
  });

  it('answers a permission from the panel', () => {
    const props = renderPanel({
      items: [{ kind: 'permission', id: 'perm-1', title: 'Bash', detail: 'rm -rf build' }],
    });

    expect(screen.getByRole('region', { name: 'Needs you (1)' })).toHaveTextContent('rm -rf build');
    fireEvent.click(screen.getByRole('button', { name: 'Allow Bash once' }));
    fireEvent.click(screen.getByRole('button', { name: 'Deny Bash' }));

    expect(props.onRespondPermission).toHaveBeenNthCalledWith(1, 'perm-1', 'allow_once');
    expect(props.onRespondPermission).toHaveBeenNthCalledWith(2, 'perm-1', 'deny');
  });

  it('offers a way back to chat only from other tabs', () => {
    const items: PanelProps['items'] = [
      { kind: 'input', id: 'input-1', title: 'Which branch?', detail: 'Answer in chat.' },
    ];
    const props = renderPanel({ items, showOpenChat: true });

    fireEvent.click(screen.getByRole('button', { name: 'Open chat' }));

    expect(props.onOpenChat).toHaveBeenCalledTimes(1);
  });

  it('shows the ticket, task progress, running agents and changed files', () => {
    const props = renderPanel({
      ticket,
      checklist: [
        { label: 'Write the fix', status: 'completed' },
        { label: 'Run tests', status: 'in_progress' },
      ],
      peers: [{ peerId: 'a', persona: 'reviewer', displayName: 'Sigrun', status: 'busy' }],
      changedFiles: 1,
    });

    expect(screen.getByRole('link', { name: 'NIU-142' })).toHaveAttribute('href', ticket.url);
    expect(screen.getByRole('region', { name: 'Tasks (1/2)' })).toHaveTextContent('Run tests');
    expect(within(screen.getByRole('region', { name: 'Running' })).getByText('Sigrun'));

    fireEvent.click(screen.getByRole('button', { name: '1 file changed' }));
    expect(props.onOpenDiffs).toHaveBeenCalledTimes(1);
  });

  it('shows a ticket that has no link as plain text', () => {
    renderPanel({ ticket: { ...ticket, url: '' } });

    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByText('NIU-142')).toBeInTheDocument();
  });
});
