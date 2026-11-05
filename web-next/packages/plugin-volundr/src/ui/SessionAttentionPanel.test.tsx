import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ConversationResourceProvider, type ConversationResourcePort } from '@niuulabs/ui';
import type { TrackerIssue } from '../models/volundr.model';
import { SessionAttentionPanel, sessionPanelHasContent } from './SessionAttentionPanel';

type PanelProps = Parameters<typeof SessionAttentionPanel>[0];

const resources: ConversationResourcePort = {
  resolve: () => null,
  load: () => Promise.reject(new Error('not loaded in this test')),
  open: vi.fn(),
};

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
  render(
    <ConversationResourceProvider port={resources}>
      <SessionAttentionPanel {...props} />
    </ConversationResourceProvider>,
  );
  return props;
}

const agents = [
  {
    id: 'agent-1',
    kind: 'subagent',
    name: 'Check the other gate entry points',
    status: 'done',
    startedAt: '2026-11-05T10:00:00.000Z',
    endedAt: '2026-11-05T10:01:52.000Z',
  },
  { id: 'agent-2', kind: 'teammate', name: 'Reviewer', status: 'failed' },
];

const deliverables = [
  { fileId: 'file-1', name: 'gate-refusal-trace.md', mime: 'text/markdown' },
  { fileId: 'file-1', name: 'gate-refusal-trace.md', mime: 'text/markdown' },
  { fileId: 'file-2', name: 'gates.png' },
];

const info: PanelProps['info'] = [
  {
    id: 'engine',
    label: 'Engine',
    value: 'Claude Sonnet 4.6',
    harness: 'claude',
    detail: 'Claude',
  },
  { id: 'host', label: 'Host', value: 'valhalla' },
  { id: 'workspace', label: 'Workspace', value: 'niuulabs/niuu', mono: true },
];

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
    for (const name of ['Needs you', 'Plan', 'Agents', 'Deliverables', 'Session', 'Ticket']) {
      expect(screen.queryByRole('region', { name })).not.toBeInTheDocument();
    }
    expect(screen.getByRole('region', { name: 'Changed files' })).toBeInTheDocument();
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
    expect(sessionPanelHasContent({ items: [], checklist: [], agents })).toBe(true);
    expect(sessionPanelHasContent({ items: [], checklist: [], deliverables })).toBe(true);
    expect(sessionPanelHasContent({ items: [], checklist: [], info })).toBe(true);
    expect(
      sessionPanelHasContent({ items: [], checklist: [], agents: [], deliverables: [], info: [] }),
    ).toBe(false);
  });

  it('answers a permission from the panel', () => {
    const props = renderPanel({
      items: [{ kind: 'permission', id: 'perm-1', title: 'Bash', detail: 'rm -rf build' }],
    });

    expect(screen.getByRole('region', { name: 'Needs you' })).toHaveTextContent('rm -rf build');
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
    const plan = screen.getByRole('region', { name: 'Plan' });
    expect(plan).toHaveTextContent('Run tests');
    expect(plan).toHaveTextContent('1/2');
    expect(screen.queryByRole('region', { name: 'Running' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '1 file changed' }));
    expect(props.onOpenDiffs).toHaveBeenCalledTimes(1);
  });

  it('puts the sections in the dock order', () => {
    renderPanel({
      items: [{ kind: 'input', id: 'i', title: 'Which branch?', detail: '' }],
      checklist: [{ label: 'Write the fix', status: 'pending' }],
      agents,
      changedFiles: 2,
      deliverables,
      info,
      ticket,
    });

    const names = screen.getAllByRole('region').map((region) => region.getAttribute('aria-label'));
    expect(names).toEqual([
      'Needs you',
      'Plan',
      'Agents',
      'Changed files',
      'Deliverables',
      'Session',
      'Ticket',
    ]);
  });

  it('shows each agent with its status and how long it ran', () => {
    renderPanel({ agents });

    const section = screen.getByRole('region', { name: 'Agents' });
    expect(section).toHaveTextContent('2');
    expect(section).toHaveTextContent('Check the other gate entry points');
    expect(section).toHaveTextContent('done · 1m');
    // An agent with no start time has no duration to show.
    expect(within(section).getByText('Reviewer').parentElement).toHaveTextContent(
      /^Reviewerfailed$/,
    );
  });

  it('breaks the changed files down by kind and leads to the diffs', () => {
    const props = renderPanel({
      changedFiles: 4,
      fileStats: { added: 1, modified: 2, deleted: 1 },
    });

    const section = screen.getByRole('region', { name: 'Changed files' });
    expect(section).toHaveTextContent('1 added · 2 modified · 1 deleted');
    fireEvent.click(within(section).getByRole('button', { name: '4 files changed' }));
    expect(props.onOpenDiffs).toHaveBeenCalledTimes(1);
  });

  it('lists a presented file once and opens it in the preview', () => {
    renderPanel({ deliverables });

    const section = screen.getByRole('region', { name: 'Deliverables' });
    expect(within(section).getAllByRole('button')).toHaveLength(2);
    fireEvent.click(within(section).getByRole('button', { name: /gate-refusal-trace.md/ }));

    expect(resources.open).toHaveBeenCalledWith({
      kind: 'presented',
      path: 'file-1',
      name: 'gate-refusal-trace.md',
      mime: 'text/markdown',
    });
    fireEvent.click(within(section).getByRole('button', { name: /gates.png/ }));
    expect(resources.open).toHaveBeenLastCalledWith({
      kind: 'presented',
      path: 'file-2',
      name: 'gates.png',
    });
  });

  it('refuses to list deliverables where nothing can open them', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() =>
      render(
        <SessionAttentionPanel
          items={[]}
          checklist={[]}
          deliverables={deliverables}
          showOpenChat={false}
          onOpenChat={vi.fn()}
          onOpenDiffs={vi.fn()}
          onRespondPermission={vi.fn()}
          onClose={vi.fn()}
        />,
      ),
    ).toThrow(/inside SessionResources/);
    error.mockRestore();
  });

  it('lists the facts about the session', () => {
    renderPanel({ info });

    const section = screen.getByRole('region', { name: 'Session' });
    expect(section).toHaveTextContent('EngineClaude Sonnet 4.6 · Claude');
    expect(section).toHaveTextContent('Hostvalhalla');
    expect(section).toHaveTextContent('Workspaceniuulabs/niuu');
    expect(within(section).getByText('Claude Sonnet 4.6')).toHaveAttribute(
      'data-harness',
      'claude',
    );
  });

  it('shows a ticket that has no link as plain text', () => {
    renderPanel({ ticket: { ...ticket, url: '' } });

    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByText('NIU-142')).toBeInTheDocument();
  });
});
