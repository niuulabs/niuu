import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMockVolundrService } from '../../adapters/mock';
import {
  MOCK_NOTIFICATIONS,
  createMockNotificationFeed,
  type MockNotificationFeed,
} from '../../adapters/notifications.mock';
import type { IntegrationConnection } from '../../models/volundr.model';
import { renderWithVolundr } from '../../testing/renderWithVolundr';
import { NotificationsPage } from './NotificationsPage';

const navigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}));

function volundrWithMessaging() {
  const service = createMockVolundrService();
  service.getIntegrations = async () => [
    {
      id: 'telegram-main',
      integration_type: 'messaging',
      slug: 'telegram',
      credential_name: 'tg-bot',
      createdAt: '2026-09-01T00:00:00Z',
      updatedAt: '2026-09-01T00:00:00Z',
    } as IntegrationConnection,
    { id: 'linear', integrationType: 'issue_tracker', createdAt: '', updatedAt: '' },
  ];
  return service;
}

function renderPage(feed: MockNotificationFeed = createMockNotificationFeed()) {
  const utils = renderWithVolundr(<NotificationsPage />, {
    notifications: feed,
    service: volundrWithMessaging(),
  });
  return { ...utils, feed };
}

function rows() {
  return screen.queryAllByTestId('notification-row');
}

async function loaded(count = MOCK_NOTIFICATIONS.length) {
  await waitFor(() => expect(rows()).toHaveLength(count));
}

beforeEach(() => {
  window.localStorage.clear();
  navigate.mockReset();
});

afterEach(() => {
  window.localStorage.clear();
});

describe('NotificationsPage', () => {
  it('shows a loading state, then the live feed with unread markers', async () => {
    renderPage();
    expect(screen.getByRole('status', { name: 'Loading notifications…' })).toBeInTheDocument();
    await loaded();
    expect(screen.getByTestId('notifications-unread')).toHaveTextContent('6 unread');
    expect(screen.getByRole('status', { name: 'Live' })).toBeInTheDocument();
    expect(screen.getAllByTestId('notification-unread-dot')).toHaveLength(6);
    const first = rows()[0]!;
    expect(first).toHaveAttribute('data-kind', 'attention');
    expect(within(first).getByText('forge-api needs your input')).toBeInTheDocument();
    expect(within(first).getByText('forge-api')).toBeInTheDocument();
    // Several hosts: rows name theirs.
    expect(within(rows()[1]!).getByText('Horde 1')).toBeInTheDocument();
    expect(screen.queryByTestId('notifications-unavailable')).toBeNull();
  });

  it('notes hosts the fan-out could not reach', async () => {
    renderPage(createMockNotificationFeed({ unavailableInstances: ['horde-1', 'horde-9'] }));
    await loaded();
    expect(screen.getByTestId('notifications-unavailable')).toHaveTextContent(
      'Some hosts are unavailable (Horde 1, horde-9)',
    );
  });

  it('shows an empty state when nothing has been reported', async () => {
    renderPage(createMockNotificationFeed({ seed: [] }));
    expect(await screen.findByText('No notifications yet')).toBeInTheDocument();
    expect(screen.getByTestId('notifications-unread')).toHaveTextContent('All read');
    expect(screen.getByRole('button', { name: 'Mark all read' })).toBeDisabled();
  });

  it('shows an error state with a working retry', async () => {
    const feed = createMockNotificationFeed();
    const list = vi.spyOn(feed, 'list').mockRejectedValueOnce(new Error('Forge is down'));
    renderPage(feed);
    expect(await screen.findByText('Could not load notifications')).toBeInTheDocument();
    expect(screen.getByText('Forge is down')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await loaded();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it('filters by kind, severity, source, host and text, and persists the filters', async () => {
    const user = userEvent.setup();
    renderPage();
    await loaded();
    const filters = screen.getByRole('group', { name: 'Notification filters' });

    await user.click(within(filters).getByRole('switch', { name: 'Error' }));
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.getByText('Benchmark run crashed')).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Active filters' })).toHaveTextContent('Kind');
    expect(window.localStorage.getItem('niuu.forge.notifications.filter')).toContain('"error"');
    await user.click(screen.getByRole('button', { name: 'Remove filter Kind' }));
    await loaded();

    await user.selectOptions(within(filters).getByLabelText('Min severity'), 'warning');
    await waitFor(() => expect(rows()).toHaveLength(2));
    await user.selectOptions(within(filters).getByLabelText('Min severity'), '');
    await loaded();

    await user.click(within(filters).getByRole('switch', { name: 'Operator' }));
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.getByText('Maintenance window tonight at 22:00')).toBeInTheDocument();
    await user.click(within(filters).getByRole('switch', { name: 'Operator' }));
    await loaded();

    await user.selectOptions(within(filters).getByLabelText('Host'), 'horde-1');
    await waitFor(() => expect(rows()).toHaveLength(2));
    await user.selectOptions(within(filters).getByLabelText('Host'), '*');
    await loaded();

    await user.selectOptions(within(filters).getByLabelText('Project'), 'proj-bench');
    await waitFor(() => expect(rows()).toHaveLength(2));
    await user.selectOptions(within(filters).getByLabelText('Project'), '');
    await loaded();

    await user.selectOptions(within(filters).getByLabelText('Session'), 'sess-bench');
    await waitFor(() => expect(rows()).toHaveLength(2));
    await user.selectOptions(within(filters).getByLabelText('Session'), '');
    await loaded();

    await user.type(screen.getByRole('searchbox', { name: 'Search' }), 'fp8');
    await waitFor(() => expect(rows()).toHaveLength(1));
    await user.type(screen.getByRole('searchbox', { name: 'Search' }), ' nothing');
    expect(await screen.findByText('No notifications match these filters')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Clear filters' }));
    await loaded();
    expect(window.localStorage.getItem('niuu.forge.notifications.filter')).not.toContain('fp8');
  });

  it('restores persisted filters and clears them all from the filter bar', async () => {
    window.localStorage.setItem(
      'niuu.forge.notifications.filter',
      JSON.stringify({ kinds: ['decision'], unreadOnly: true }),
    );
    const user = userEvent.setup();
    renderPage();
    await loaded(1);
    expect(screen.getByText('Chose fp8 for the sweep')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Clear all filters' }));
    await loaded();
  });

  it('marks everything read', async () => {
    const user = userEvent.setup();
    renderPage();
    await loaded();
    await user.click(screen.getByRole('button', { name: 'Mark all read' }));
    await waitFor(() =>
      expect(screen.getByTestId('notifications-unread')).toHaveTextContent('All read'),
    );
    expect(screen.queryAllByTestId('notification-unread-dot')).toHaveLength(0);
    await user.click(
      within(screen.getByRole('group', { name: 'Notification filters' })).getByRole('switch', {
        name: 'Unread only',
      }),
    );
    expect(await screen.findByText('No notifications match these filters')).toBeInTheDocument();
  });

  it('marks read through a row, per host, leaving newer rows unread', async () => {
    const user = userEvent.setup();
    renderPage();
    await loaded();
    // Row 3 is thor seq 6; below it are horde seq 3 and thor seqs 5 and 4.
    await user.click(within(rows()[2]!).getByRole('button', { name: 'Read through here' }));
    await waitFor(() =>
      expect(screen.getByTestId('notifications-unread')).toHaveTextContent('2 unread'),
    );
    expect(rows().map((row) => row.hasAttribute('data-unread'))).toEqual([
      true,
      true,
      false,
      false,
      false,
      false,
    ]);
  });

  it('reports a failed mark-read', async () => {
    const feed = createMockNotificationFeed();
    vi.spyOn(feed, 'markRead').mockRejectedValue(new Error('read-state offline'));
    const user = userEvent.setup();
    renderPage(feed);
    await loaded();
    await user.click(screen.getByRole('button', { name: 'Mark all read' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('read-state offline');
  });

  it('opens the session at the notification, or expands rows without one', async () => {
    const user = userEvent.setup();
    renderPage();
    await loaded();
    await user.click(
      screen.getByRole('button', { name: /Benchmark run crashed — open gpu-bench/ }),
    );
    expect(navigate).toHaveBeenCalledWith({
      to: '/volundr/session/$sessionId',
      params: { sessionId: 'sess-bench' },
      hash: 'notification-n-horde-4',
    });

    const operator = screen.getByRole('button', { name: /Maintenance window tonight at 22:00$/ });
    await user.click(operator);
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Hide details' })).toBeInTheDocument();
  });

  it('shows the full body and delivery status in the expanded row', async () => {
    const user = userEvent.setup();
    renderPage();
    await loaded();
    const crashed = rows()[1]!;
    await user.click(within(crashed).getByRole('button', { name: 'Show details' }));
    expect(within(crashed).getByText('RuntimeError: CUDA out of memory')).toBeInTheDocument();
    const delivery = await within(crashed).findByTestId('notification-delivery');
    expect(delivery).toHaveTextContent('Telegram');
    expect(delivery).toHaveTextContent('Delivered');

    const milestone = rows()[2]!;
    await user.click(within(milestone).getByRole('button', { name: 'Show details' }));
    expect(
      await within(milestone).findByText('Not sent anywhere else — no delivery rule matched.'),
    ).toBeInTheDocument();
    expect(within(milestone).getByRole('link', { name: 'PR #412' })).toHaveAttribute(
      'target',
      '_blank',
    );
    await user.click(within(milestone).getByRole('button', { name: 'Hide details' }));
    expect(within(milestone).queryByTestId('markdown-content')).toBeNull();
  });

  it('reports delivery status failures', async () => {
    const feed = createMockNotificationFeed();
    vi.spyOn(feed, 'deliveries').mockRejectedValue(new Error('nope'));
    const user = userEvent.setup();
    renderPage(feed);
    await loaded();
    await user.click(within(rows()[0]!).getByRole('button', { name: 'Show details' }));
    expect(await screen.findByText('Could not load delivery status.')).toBeInTheDocument();
  });

  it('moves between rows with j/k and the arrow keys', async () => {
    const user = userEvent.setup();
    renderPage();
    await loaded();
    const opens = () =>
      rows().map((row) => row.querySelector<HTMLElement>('[data-notification-open]')!);
    opens()[0]!.focus();
    await user.keyboard('j');
    expect(opens()[1]).toHaveFocus();
    await user.keyboard('{ArrowDown}');
    expect(opens()[2]).toHaveFocus();
    await user.keyboard('k');
    expect(opens()[1]).toHaveFocus();
    await user.keyboard('{ArrowUp}{ArrowUp}{ArrowUp}');
    expect(opens()[0]).toHaveFocus();
    await user.keyboard('x');
    expect(opens()[0]).toHaveFocus();
    // From a row's action button, j still moves to the next row.
    within(rows()[3]!).getByRole('button', { name: 'Show details' }).focus();
    await user.keyboard('j');
    expect(opens()[4]).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(navigate).toHaveBeenCalledWith(
      expect.objectContaining({ params: { sessionId: 'sess-bench' } }),
    );
  });

  it('pages older notifications on demand', async () => {
    const feed = createMockNotificationFeed();
    const list = feed.list;
    feed.list = (filter, cursor) => list(filter, { ...cursor, limit: 4 });
    const user = userEvent.setup();
    renderPage(feed);
    await loaded(4);
    await user.click(screen.getByRole('button', { name: 'Load older' }));
    await loaded();
    expect(screen.queryByRole('button', { name: 'Load older' })).toBeNull();
  });

  it('offers older pages when filters match nothing loaded yet', async () => {
    const feed = createMockNotificationFeed();
    const list = feed.list;
    const pages = vi.fn((filter: Parameters<typeof list>[0], cursor?: Parameters<typeof list>[1]) =>
      list(filter, { ...cursor, limit: 5 }),
    );
    feed.list = pages;
    const user = userEvent.setup();
    renderPage(feed);
    await loaded(5);
    await user.type(screen.getByRole('searchbox', { name: 'Search' }), 'maintenance');
    expect(
      await screen.findByText('Older notifications may still match — load more or clear filters.'),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Load older' }));
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(rows()[0]).toHaveTextContent('Maintenance window tonight at 22:00');
    expect(pages).toHaveBeenLastCalledWith(expect.anything(), { before: '5' });
  });

  it('prepends notifications as they arrive', async () => {
    const { feed } = renderPage();
    await loaded();
    act(() =>
      feed.emit({
        ...MOCK_NOTIFICATIONS[0]!,
        id: 'n-live',
        seq: 40,
        title: 'Deploy finished',
        createdAt: '2026-09-23T09:30:00Z',
      }),
    );
    await waitFor(() => expect(rows()[0]).toHaveTextContent('Deploy finished'));
    await waitFor(() =>
      expect(screen.getByTestId('notifications-unread')).toHaveTextContent('7 unread'),
    );
  });
});

describe('Delivery rules panel', () => {
  async function openRules(user: ReturnType<typeof userEvent.setup>) {
    await loaded();
    await user.click(screen.getByRole('button', { name: 'Delivery rules' }));
    return screen.getByRole('complementary', { name: 'Delivery rules' });
  }

  it('lists rules and creates one with validation', async () => {
    const user = userEvent.setup();
    renderPage();
    const panel = await openRules(user);
    const rule = await within(panel).findByTestId('notification-rule');
    expect(rule).toHaveTextContent('Critical to Telegram');
    expect(rule).toHaveTextContent('Any kind · Critical+ → Telegram');
    expect(rule).toHaveTextContent('Quiet 22:00–07:00 Europe/London');

    await user.click(within(panel).getByRole('button', { name: 'New rule' }));
    const form = within(panel).getByRole('form', { name: 'New delivery rule' });
    await user.click(within(form).getByRole('button', { name: 'Create rule' }));
    expect(within(form).getByText('Give the rule a name.')).toBeInTheDocument();
    expect(within(form).getByText('Telegram needs a messaging connection.')).toBeInTheDocument();

    await user.type(within(form).getByLabelText('Name'), 'Decisions to push');
    await user.selectOptions(within(form).getByLabelText('Deliver to'), 'push');
    expect(within(form).queryByLabelText('Messaging connection')).toBeNull();
    await user.click(within(form).getByRole('checkbox', { name: 'Decision' }));
    await user.click(within(form).getByRole('checkbox', { name: 'Agent' }));
    await user.selectOptions(within(form).getByLabelText('Minimum severity'), 'warning');
    await user.type(
      within(form).getByLabelText('Project ids (comma separated, empty = any)'),
      'proj-a, proj-b,',
    );
    await user.click(within(form).getByRole('button', { name: 'Create rule' }));

    await waitFor(() => expect(within(panel).getAllByTestId('notification-rule')).toHaveLength(2));
    expect(within(panel).getAllByTestId('notification-rule')[1]).toHaveTextContent(
      'Decision · Warning+ · Agent · 2 project(s) → Push (iOS)',
    );
  });

  it('requires and offers a messaging connection for Telegram, with quiet hours', async () => {
    const user = userEvent.setup();
    const { feed } = renderPage();
    const createRule = vi.spyOn(feed, 'createRule');
    const panel = await openRules(user);
    await user.click(within(panel).getByRole('button', { name: 'New rule' }));
    const form = within(panel).getByRole('form', { name: 'New delivery rule' });
    await user.type(within(form).getByLabelText('Name'), 'Night errors');
    const connection = within(form).getByLabelText('Messaging connection');
    expect(
      within(connection)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual(['Choose a connection…', 'telegram · tg-bot']);
    await user.selectOptions(connection, 'telegram-main');
    await user.click(
      within(form).getByRole('checkbox', { name: 'Hold messages during quiet hours' }),
    );
    const start = within(form).getByLabelText('From');
    await user.clear(start);
    await user.click(within(form).getByRole('button', { name: 'Create rule' }));
    expect(within(form).getByText('Quiet hours use 24-hour HH:MM times.')).toBeInTheDocument();
    fireEvent.change(start, { target: { value: '23:30' } });
    await user.clear(within(form).getByLabelText('Timezone'));
    await user.click(within(form).getByRole('button', { name: 'Create rule' }));
    expect(within(form).getByText('Quiet hours need a timezone.')).toBeInTheDocument();
    await user.type(within(form).getByLabelText('Timezone'), 'Europe/Oslo');
    fireEvent.change(within(form).getByLabelText('Until'), { target: { value: '06:00' } });
    await user.selectOptions(within(form).getByLabelText('Still send at or above'), 'warning');
    await user.click(within(form).getByRole('button', { name: 'Create rule' }));
    await waitFor(() => expect(createRule).toHaveBeenCalled());
    expect(createRule.mock.calls[0]![0]).toMatchObject({
      name: 'Night errors',
      sink: 'telegram',
      integrationConnectionId: 'telegram-main',
      quietHours: {
        start: '23:30',
        end: '06:00',
        timezone: 'Europe/Oslo',
        allowMinSeverity: 'warning',
      },
    });
  });

  it('edits, disables and deletes a rule', async () => {
    const user = userEvent.setup();
    renderPage();
    const panel = await openRules(user);
    await within(panel).findByTestId('notification-rule');
    await user.click(within(panel).getByRole('button', { name: 'Edit Critical to Telegram' }));
    const form = within(panel).getByRole('form', { name: 'Edit Critical to Telegram' });
    const name = within(form).getByLabelText('Name');
    await user.clear(name);
    await user.type(name, 'Critical everywhere');
    await user.click(within(form).getByRole('checkbox', { name: 'Enabled' }));
    await user.click(
      within(form).getByRole('checkbox', { name: 'Hold messages during quiet hours' }),
    );
    await user.click(within(form).getByRole('button', { name: 'Save rule' }));
    const rule = await within(panel).findByText('Critical everywhere');
    expect(rule.closest('li')).toHaveTextContent('Off');
    expect(rule.closest('li')).not.toHaveTextContent('Quiet');

    await user.click(within(panel).getByRole('button', { name: 'Delete Critical everywhere' }));
    await user.click(within(panel).getByRole('button', { name: 'Keep' }));
    await user.click(within(panel).getByRole('button', { name: 'Delete Critical everywhere' }));
    await user.click(within(panel).getByRole('button', { name: 'Delete' }));
    expect(
      await within(panel).findByText(
        'No delivery rules. Notifications stay in Forge until you add one.',
      ),
    ).toBeInTheDocument();
  });

  it('cancels editing, reports save and load failures, and closes with Escape', async () => {
    const feed = createMockNotificationFeed();
    vi.spyOn(feed, 'updateRule').mockRejectedValue(new Error('rule store offline'));
    const listRules = vi.spyOn(feed, 'listRules');
    const user = userEvent.setup();
    renderPage(feed);
    const panel = await openRules(user);
    await within(panel).findByTestId('notification-rule');
    await user.click(within(panel).getByRole('button', { name: 'Edit Critical to Telegram' }));
    await user.click(within(panel).getByRole('button', { name: 'Save rule' }));
    expect(await within(panel).findByRole('alert')).toHaveTextContent('rule store offline');
    await user.click(within(panel).getByRole('button', { name: 'Cancel' }));
    expect(within(panel).queryByRole('form')).toBeNull();

    listRules.mockRejectedValueOnce(new Error('rules unavailable'));
    await user.selectOptions(within(panel).getByRole('combobox', { name: 'Host' }), 'horde-1');
    expect(await within(panel).findByText(/rules unavailable/)).toBeInTheDocument();
    expect(listRules).toHaveBeenLastCalledWith({ instanceId: 'horde-1' });
    await user.click(within(panel).getByRole('button', { name: 'Retry' }));
    await within(panel).findByTestId('notification-rule');

    within(panel).getByRole('button', { name: 'New rule' }).focus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('complementary', { name: 'Delivery rules' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Delivery rules' })).toHaveFocus();
  });

  it('reports sink and delete failures', async () => {
    const feed = createMockNotificationFeed();
    vi.spyOn(feed, 'sinks').mockRejectedValue(new Error('sinks down'));
    vi.spyOn(feed, 'deleteRule').mockRejectedValue(new Error('cannot delete'));
    const user = userEvent.setup();
    renderPage(feed);
    const panel = await openRules(user);
    expect(await within(panel).findByText(/sinks down/)).toBeInTheDocument();
    expect(within(panel).getByRole('button', { name: 'New rule' })).toBeDisabled();
    await user.click(
      await within(panel).findByRole('button', { name: 'Delete Critical to Telegram' }),
    );
    await user.click(within(panel).getByRole('button', { name: 'Delete' }));
    expect(await within(panel).findByText(/cannot delete/)).toBeInTheDocument();
    await user.click(within(panel).getByRole('button', { name: 'Close delivery rules' }));
    expect(screen.queryByRole('complementary', { name: 'Delivery rules' })).toBeNull();
  });
});

describe('NotificationsPage host names and edge cases', () => {
  it('names hosts from the Forge targets and explains unknown failures', async () => {
    const service = volundrWithMessaging();
    service.getTargets = async () => [
      {
        id: 'thor',
        slug: 'thor',
        name: 'Thor box',
        baseUrl: '',
        enabled: true,
        isDefault: true,
        tags: [],
      },
      {
        id: 'horde-1',
        slug: 'horde-one',
        name: '',
        baseUrl: '',
        enabled: true,
        isDefault: false,
        tags: [],
      },
    ];
    service.getIntegrations = async () => [
      {
        id: 'bare-connection',
        integration_type: 'Messaging',
        createdAt: '',
        updatedAt: '',
      } as never,
    ];
    const feed = createMockNotificationFeed();
    vi.spyOn(feed, 'markRead').mockRejectedValue('offline');
    window.localStorage.setItem(
      'niuu.forge.notifications.filter',
      JSON.stringify({ sessionId: 'sess-gone' }),
    );
    const user = userEvent.setup();
    renderWithVolundr(<NotificationsPage />, { notifications: feed, service });
    expect(await screen.findByText('No notifications match these filters')).toBeInTheDocument();
    const filters = screen.getByRole('group', { name: 'Notification filters' });
    expect(within(filters).getByLabelText('Session')).toHaveValue('sess-gone');
    await user.click(screen.getByRole('button', { name: 'Clear filters' }));
    await loaded();
    expect(within(rows()[0]!).getByText('Thor box')).toBeInTheDocument();
    expect(within(rows()[1]!).getByText('horde-one')).toBeInTheDocument();
    expect(
      within(within(filters).getByLabelText('Host'))
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['All hosts', 'horde-one', 'Thor box']);

    await user.click(screen.getByRole('button', { name: 'Mark all read' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The Forge did not answer.');

    await user.click(screen.getByRole('button', { name: 'Delivery rules' }));
    const panel = screen.getByRole('complementary', { name: 'Delivery rules' });
    await user.click(
      await within(panel).findByRole('button', { name: 'Edit Critical to Telegram' }),
    );
    const connection = within(panel).getByLabelText('Messaging connection');
    expect(within(connection).getByRole('option', { name: 'bare-connection' })).toBeInTheDocument();
  });

  it('shows progress while an older page loads', async () => {
    const feed = createMockNotificationFeed();
    const list = feed.list;
    let release: () => void = () => {};
    feed.list = async (filter, cursor) => {
      if (cursor?.before) await new Promise<void>((resolve) => (release = resolve));
      return list(filter, { ...cursor, limit: 3 });
    };
    const user = userEvent.setup();
    renderPage(feed);
    await loaded(3);
    await user.click(screen.getByRole('button', { name: 'Load older' }));
    expect(screen.getByRole('button', { name: 'Loading…' })).toBeDisabled();
    act(() => release());
    await loaded();
  });
});
