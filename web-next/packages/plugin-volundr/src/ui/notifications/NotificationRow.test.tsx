import { describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMockNotificationFeed } from '../../adapters/notifications.mock';
import type { SessionNotification } from '../../domain/notifications';
import { renderWithVolundr } from '../../testing/renderWithVolundr';
import { NotificationRow, type NotificationRowProps } from './NotificationRow';

const base: SessionNotification = {
  id: 'n-9',
  instanceId: null,
  seq: 9,
  sessionId: 'sess-9',
  sessionSeq: 90,
  sessionName: null,
  ownerId: 'u',
  projectId: null,
  kind: 'decision',
  severity: 'info',
  source: 'agent',
  title: 'Picked Postgres',
  body: '',
  links: [
    { label: 'Runbook', url: '/docs/runbook', kind: 'url', fileId: null },
    { label: 'Evil', url: 'javascript:alert(1)', kind: 'url', fileId: null },
    { label: 'report.pdf', url: null, kind: 'file', fileId: 'f_1' },
  ],
  engine: null,
  model: null,
  correlationId: null,
  createdAt: '2026-09-23T10:00:00Z',
  read: true,
};

function renderRow(overrides: Partial<NotificationRowProps> = {}) {
  const feed = createMockNotificationFeed({
    deliveries: {
      'n-9': [
        {
          id: 'd1',
          ruleId: 'r1',
          sink: 'pager',
          status: 'dead',
          attempts: 5,
          lastError: 'HTTP 410 from webhook',
          deliveredAt: null,
          nextAttemptAt: null,
          updatedAt: '2026-09-23T10:05:00Z',
        },
        {
          id: 'd2',
          ruleId: 'r2',
          sink: 'telegram',
          status: 'suppressed',
          attempts: 0,
          lastError: null,
          deliveredAt: null,
          nextAttemptAt: null,
          updatedAt: null,
        },
      ],
    },
  });
  const props: NotificationRowProps = {
    notification: base,
    read: true,
    hostLabel: null,
    sinkLabels: { telegram: 'Telegram' },
    onOpen: vi.fn(),
    onMarkReadThrough: vi.fn(),
    markingRead: false,
    ...overrides,
  };
  renderWithVolundr(
    <ul>
      <NotificationRow {...props} />
    </ul>,
    { notifications: feed },
  );
  return props;
}

describe('NotificationRow', () => {
  it('renders a read row without unread affordances', async () => {
    const user = userEvent.setup();
    const props = renderRow();
    const row = screen.getByTestId('notification-row');
    expect(row).not.toHaveAttribute('data-unread');
    expect(screen.queryByTestId('notification-unread-dot')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Read through here' })).toBeNull();
    const open = screen.getByRole('button', { name: 'Decision: Picked Postgres — open session' });
    await user.click(open);
    expect(props.onOpen).toHaveBeenCalledWith(base);
  });

  it('shows unread rows with a read-through action and chips', async () => {
    const user = userEvent.setup();
    const props = renderRow({
      read: false,
      hostLabel: 'Thor',
      notification: { ...base, engine: 'codex', model: 'gpt-6-astra', severity: 'critical' },
    });
    expect(screen.getByTestId('notification-unread-dot')).toBeInTheDocument();
    expect(screen.getByText('Thor')).toBeInTheDocument();
    expect(screen.getByTestId('cli-badge')).toBeInTheDocument();
    expect(screen.getByTestId('model-chip')).toHaveTextContent('gpt-6-astra');
    expect(screen.getByText('Critical')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Read through here' }));
    expect(props.onMarkReadThrough).toHaveBeenCalledWith(expect.objectContaining({ id: 'n-9' }));
  });

  it('expands to show links safely, an empty body and every delivery outcome', async () => {
    const user = userEvent.setup();
    renderRow();
    await user.click(screen.getByRole('button', { name: 'Show details' }));
    expect(screen.getByText('No details.')).toBeInTheDocument();
    const links = screen.getByRole('list', { name: 'Links' });
    expect(within(links).getByRole('link', { name: 'Runbook' })).not.toHaveAttribute('target');
    expect(within(links).queryByRole('link', { name: 'Evil' })).toBeNull();
    expect(within(links).getByText('report.pdf')).toHaveAttribute(
      'title',
      'Open it from the session',
    );
    const deliveries = await screen.findAllByTestId('notification-delivery');
    expect(deliveries[0]).toHaveTextContent('pager');
    expect(deliveries[0]).toHaveTextContent('Failed');
    expect(deliveries[0]).toHaveTextContent('5 attempts');
    expect(deliveries[0]).toHaveTextContent('HTTP 410 from webhook');
    expect(deliveries[1]).toHaveTextContent('Telegram');
    expect(deliveries[1]).toHaveTextContent('Held (quiet hours or rate limit)');
    expect(deliveries[1]!.querySelector('time')).toBeNull();
  });

  it('never repeats a title that an older reply summary copied into its body', async () => {
    const user = userEvent.setup();
    renderRow({ notification: { ...base, kind: 'reply_ready', title: 'done', body: ' done ' } });
    expect(screen.getAllByText('done')).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: 'Show details' }));
    expect(screen.getByText('No details.')).toBeInTheDocument();
  });

  it('expands instead of navigating when there is no session', async () => {
    const user = userEvent.setup();
    const props = renderRow({ notification: { ...base, sessionId: null } });
    await user.click(screen.getByRole('button', { name: 'Decision: Picked Postgres' }));
    expect(props.onOpen).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Hide details' })).toBeInTheDocument();
  });
});
