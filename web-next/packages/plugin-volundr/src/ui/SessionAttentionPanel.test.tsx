import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { TrackerIssue } from '../models/volundr.model';
import { SessionAttentionPanel, sessionPanelHasContent } from './SessionAttentionPanel';

type PanelProps = Parameters<typeof SessionAttentionPanel>[0];

function renderPanel(overrides: Partial<PanelProps> = {}) {
  const props: PanelProps = {
    items: [],
    checklist: [],
    showOpenChat: false,
    onOpenChat: vi.fn(),
    onOpenDiffs: vi.fn(),
    onRespondPermission: vi.fn(),
    onClose: vi.fn(),
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
  it('hides empty sections and closes from its own header', () => {
    const props = renderPanel({ changedFiles: 2 });

    expect(screen.getByText('Overview')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Waiting on you' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Ticket' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hide session panel' }));
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it('has content only when it shows something the other columns do not', () => {
    expect(sessionPanelHasContent({ items: [], checklist: [] })).toBe(false);
    expect(sessionPanelHasContent({ items: [], checklist: [], changedFiles: 0 })).toBe(false);
    expect(sessionPanelHasContent({ items: [], checklist: [], changedFiles: 1 })).toBe(true);
    expect(sessionPanelHasContent({ items: [], checklist: [], ticket })).toBe(true);
    expect(
      sessionPanelHasContent({ items: [], checklist: [{ label: 'x', status: 'pending' }] }),
    ).toBe(true);
    expect(
      sessionPanelHasContent({
        items: [{ kind: 'input', id: 'i', title: 'q', detail: '' }],
        checklist: [],
      }),
    ).toBe(true);
  });

  it('answers a permission from the panel', () => {
    const props = renderPanel({
      items: [{ kind: 'permission', id: 'perm-1', title: 'Bash', detail: 'rm -rf build' }],
    });

    expect(screen.getByText('Needs you (1)')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Waiting on you' })).toHaveTextContent(
      'rm -rf build',
    );
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

  it('shows the ticket, task progress and changed files', () => {
    const props = renderPanel({
      ticket,
      checklist: [
        { label: 'Write the fix', status: 'completed' },
        { label: 'Run tests', status: 'in_progress' },
      ],
      changedFiles: 1,
    });

    expect(screen.getByRole('link', { name: 'NIU-142' })).toHaveAttribute('href', ticket.url);
    expect(screen.getByRole('region', { name: 'Tasks (1/2)' })).toHaveTextContent('Run tests');
    expect(screen.queryByRole('region', { name: 'Running' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '1 file changed' }));
    expect(props.onOpenDiffs).toHaveBeenCalledTimes(1);
  });

  it('shows a ticket that has no link as plain text', () => {
    renderPanel({ ticket: { ...ticket, url: '' } });

    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByText('NIU-142')).toBeInTheDocument();
  });
});
