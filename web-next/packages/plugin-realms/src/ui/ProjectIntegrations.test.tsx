import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMockVolundrService, type IVolundrService } from '@niuulabs/plugin-volundr';
import { renderRealms } from '../testing/renderRealms';
import {
  errorDetail,
  isForbidden,
  routeFor,
  teamInstallSignal,
} from '../application/useProjectIntegrations';

const PAGE = '/realms/projects/project-yggdrasil';
const SLACK = 'conn-slack-team';

/** The mock service with the viewer swapped for someone who is not an owner. */
function asMember(base: IVolundrService): IVolundrService {
  return {
    ...base,
    getIdentity: async () => ({ ...(await base.getIdentity()), userId: 'alice' }),
    getProjectMembers: async () => [
      {
        project_id: 'project-yggdrasil',
        user_id: 'u1',
        role: 'owner',
        expertise: '',
        skills: [],
        display_name: 'Dev',
        email: 'dev@niuu.world',
      },
      {
        project_id: 'project-yggdrasil',
        user_id: 'alice',
        role: 'viewer',
        expertise: '',
        skills: [],
        display_name: 'Alice',
        email: 'alice@example.com',
      },
    ],
  };
}

async function card() {
  return screen.findByTestId('project-integrations');
}

/** A ``storage`` event as another window's write raises it: only ``key`` and ``newValue``. */
function storageSignal(key: string, newValue: string): Event {
  const event = new Event('storage');
  Object.defineProperties(event, { key: { value: key }, newValue: { value: newValue } });
  return event;
}

describe('ProjectIntegrations', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('shows the owner what is connected, where messages go and what used it', async () => {
    const user = userEvent.setup();
    renderRealms(PAGE);
    const integrations = await card();
    expect(screen.getByRole('heading', { name: 'Team integrations' })).toBeInTheDocument();
    expect(screen.getByText(/They act as the team's bot, never as a person\./)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Connect' })).toBeInTheDocument();

    const slack = await within(integrations).findByTestId(`project-integration-${SLACK}`);
    expect(within(slack).getByText('Slack')).toBeInTheDocument();
    expect(within(slack).getByText('Connected')).toBeInTheDocument();
    expect(within(slack).getByText('#eng · installed by Dev')).toBeInTheDocument();
    expect(within(slack).getByRole('button', { name: 'Remove Slack' })).toBeInTheDocument();
    expect(within(slack).getByText('When')).toBeInTheDocument();
    expect(within(slack).getByText('Send to')).toBeInTheDocument();
    for (const label of [
      'Needs a decision',
      'An agent asks a person',
      'Progress and finished work',
      'Errors',
    ]) {
      expect(within(slack).getByRole('combobox', { name: `Send ${label} to` })).toHaveValue('none');
    }
    const options = (name: string) =>
      within(within(slack).getByRole('combobox', { name }))
        .getAllByRole('option')
        .map((o) => o.textContent);
    expect(options('Send Needs a decision to')).toEqual([
      '#eng',
      'Message each approver',
      'Nobody',
    ]);
    expect(options('Send An agent asks a person to')).toEqual([
      'Message that person',
      '#eng',
      'Nobody',
    ]);
    expect(options('Send Progress and finished work to')).toEqual(['#eng', 'Nobody']);
    expect(options('Send Errors to')).toEqual(['#eng', 'Message the session owner', 'Nobody']);

    const usage = await within(integrations).findByTestId('project-integration-usage');
    expect(await within(usage).findAllByText('Dev')).toHaveLength(3);
    expect(within(usage).getByText('#eng message 5')).toBeInTheDocument();
    expect(within(usage).queryByText('#eng message 1')).not.toBeInTheDocument();
    await user.click(within(usage).getByRole('button', { name: 'Show older' }));
    expect(await within(usage).findByText('#eng message 1')).toBeInTheDocument();
    expect(within(usage).queryByRole('button', { name: 'Show older' })).not.toBeInTheDocument();
    expect(within(integrations).queryByText(/Only owners connect/)).not.toBeInTheDocument();
  });

  it('sets a route with PUT and clears it with DELETE', async () => {
    const user = userEvent.setup();
    const base = createMockVolundrService();
    const setTeamRoute = vi.fn(base.setTeamRoute);
    const removeTeamRoute = vi.fn(base.removeTeamRoute);
    renderRealms(PAGE, { volundr: { ...base, setTeamRoute, removeTeamRoute } });
    const slack = await screen.findByTestId(`project-integration-${SLACK}`);

    const decision = within(slack).getByRole('combobox', { name: 'Send Needs a decision to' });
    await user.selectOptions(decision, 'Message each approver');
    await waitFor(() =>
      expect(setTeamRoute).toHaveBeenCalledWith(
        expect.objectContaining({ id: 'project-yggdrasil' }),
        'needs_decision',
        { connection_id: SLACK, deliver_to: 'recipient', channel: '', enabled: true },
      ),
    );
    await waitFor(() => expect(decision).toHaveValue('recipient'));

    await user.selectOptions(
      within(slack).getByRole('combobox', { name: 'Send Errors to' }),
      '#eng',
    );
    await waitFor(() =>
      expect(setTeamRoute).toHaveBeenCalledWith(expect.anything(), 'errors', {
        connection_id: SLACK,
        deliver_to: 'channel',
        channel: '#eng',
        enabled: true,
      }),
    );

    await user.selectOptions(decision, 'Nobody');
    await waitFor(() =>
      expect(removeTeamRoute).toHaveBeenCalledWith(
        expect.objectContaining({ id: 'project-yggdrasil' }),
        'needs_decision',
      ),
    );
    await waitFor(() => expect(decision).toHaveValue('none'));
  });

  it('shows the API’s detail when a route change is refused', async () => {
    const user = userEvent.setup();
    const base = createMockVolundrService();
    const setTeamRoute = vi.fn(async () => {
      throw Object.assign(new Error('API request failed: 403'), {
        status: 403,
        detail: 'Only project owners can route notifications',
      });
    });
    renderRealms(PAGE, { volundr: { ...base, setTeamRoute } });
    const slack = await screen.findByTestId(`project-integration-${SLACK}`);
    await user.selectOptions(
      within(slack).getByRole('combobox', { name: 'Send Progress and finished work to' }),
      '#eng',
    );
    expect(await within(slack).findByRole('alert')).toHaveTextContent(
      'Only project owners can route notifications',
    );
  });

  it('removes a connection', async () => {
    const user = userEvent.setup();
    renderRealms(PAGE);
    const slack = await screen.findByTestId(`project-integration-${SLACK}`);
    await user.click(within(slack).getByRole('button', { name: 'Remove Slack' }));
    await waitFor(() =>
      expect(screen.queryByTestId(`project-integration-${SLACK}`)).not.toBeInTheDocument(),
    );
    expect(await screen.findByText('Nothing is connected yet.')).toBeInTheDocument();
  });

  it('shows a member everything read-only', async () => {
    renderRealms(PAGE, { volundr: asMember(createMockVolundrService()) });
    const integrations = await card();
    const slack = await within(integrations).findByTestId(`project-integration-${SLACK}`);
    expect(within(slack).getByText('Connected')).toBeInTheDocument();
    expect(within(slack).getByRole('combobox', { name: 'Send Errors to' })).toBeDisabled();
    expect(within(slack).queryByRole('button', { name: /Remove/ })).not.toBeInTheDocument();
    expect(within(integrations).queryByRole('button', { name: 'Connect' })).not.toBeInTheDocument();
    expect(
      within(integrations).getByText('Only owners connect, route or remove.'),
    ).toBeInTheDocument();
    expect(await within(integrations).findByText('#eng message 5')).toBeInTheDocument();
  });

  it('treats a 403 on the reads as read-only and says so', async () => {
    const base = createMockVolundrService();
    const forbidden = async () => {
      throw Object.assign(new Error('API request failed: 403'), { status: 403, detail: 'no' });
    };
    renderRealms(PAGE, { volundr: { ...base, getTeamRoutes: forbidden } });
    const integrations = await card();
    expect(await within(integrations).findByText(/Only owners connect/)).toBeInTheDocument();
    expect(within(integrations).queryByRole('button', { name: 'Connect' })).not.toBeInTheDocument();
    expect(within(integrations).queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows a load error and an empty usage list', async () => {
    const base = createMockVolundrService();
    renderRealms(PAGE, {
      volundr: {
        ...base,
        getTeamIntegrationCatalog: async () => {
          throw Object.assign(new Error('API request failed: 500'), { detail: 'catalog down' });
        },
        getTeamIntegrationUsage: async () => ({ items: [], next_before: null }),
      },
    });
    const integrations = await card();
    expect(await within(integrations).findByText('catalog down')).toBeInTheDocument();
    expect(
      await within(integrations).findByText('Nothing has used these yet.'),
    ).toBeInTheDocument();
  });

  it('connects a service account from a form built from the catalog', async () => {
    const user = userEvent.setup();
    const base = createMockVolundrService();
    const connectTeamServiceAccount = vi.fn(base.connectTeamServiceAccount);
    renderRealms(PAGE, { volundr: { ...base, connectTeamServiceAccount } });
    await screen.findByTestId(`project-integration-${SLACK}`);
    const connect = screen.getByRole('button', { name: 'Connect' });
    await waitFor(() => expect(connect).toBeEnabled());
    await user.click(connect);
    const picker = await screen.findByTestId('project-integration-connect');
    await user.click(within(picker).getByRole('button', { name: 'Jira' }));

    const form = within(picker).getByRole('form', { name: 'Connect Jira' });
    expect(within(form).getByLabelText('Site URL *')).toBeInTheDocument();
    expect(within(form).getByLabelText('Cloud ID *')).toBeInTheDocument();
    expect(within(form).getByLabelText('Default project key')).toBeInTheDocument();
    expect(within(form).getByLabelText('Client secret *')).toHaveAttribute('type', 'password');
    const submit = within(form).getByRole('button', { name: 'Connect service account' });
    expect(submit).toBeDisabled();

    await user.type(within(form).getByLabelText('Client ID *'), 'cid');
    await user.type(within(form).getByLabelText('Client secret *'), 'shh');
    await user.type(within(form).getByLabelText('Site URL *'), 'https://acme.atlassian.net');
    await user.type(within(form).getByLabelText('Cloud ID *'), 'cloud-1');
    expect(submit).toBeEnabled();
    await user.click(submit);

    await waitFor(() =>
      expect(connectTeamServiceAccount).toHaveBeenCalledWith(
        expect.objectContaining({ id: 'project-yggdrasil' }),
        {
          slug: 'jira',
          credential: { client_id: 'cid', client_secret: 'shh' },
          config: { site_url: 'https://acme.atlassian.net', cloud_id: 'cloud-1' },
        },
      ),
    );
    expect(
      await screen.findByText('acme.atlassian.net · service account · installed by Dev'),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('project-integration-connect')).not.toBeInTheDocument();
  });

  it('shows the detail the API sent when a service account is refused', async () => {
    const user = userEvent.setup();
    const base = createMockVolundrService();
    renderRealms(PAGE, {
      volundr: {
        ...base,
        connectTeamServiceAccount: async () => {
          throw Object.assign(new Error('API request failed: 502'), {
            status: 502,
            detail: 'Jira rejected the client credentials',
          });
        },
      },
    });
    await screen.findByTestId(`project-integration-${SLACK}`);
    const connect = screen.getByRole('button', { name: 'Connect' });
    await waitFor(() => expect(connect).toBeEnabled());
    await user.click(connect);
    const picker = await screen.findByTestId('project-integration-connect');
    await user.click(within(picker).getByRole('button', { name: 'Jira' }));
    await user.type(within(picker).getByLabelText('Client ID *'), 'cid');
    await user.type(within(picker).getByLabelText('Client secret *'), 'shh');
    await user.type(within(picker).getByLabelText('Site URL *'), 'https://acme.atlassian.net');
    await user.type(within(picker).getByLabelText('Cloud ID *'), 'cloud-1');
    await user.click(within(picker).getByRole('button', { name: 'Connect service account' }));
    expect(await within(picker).findByRole('alert')).toHaveTextContent(
      'Jira rejected the client credentials',
    );
  });

  it('installs an app in a popup and refreshes when the callback signals it', async () => {
    const user = userEvent.setup();
    const base = createMockVolundrService();
    const startTeamOAuth = vi.fn(base.startTeamOAuth);
    const popup = { location: { href: '' }, close: vi.fn() };
    const open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
    renderRealms(PAGE, { volundr: { ...base, startTeamOAuth } });
    await screen.findByTestId(`project-integration-${SLACK}`);
    const connect = screen.getByRole('button', { name: 'Connect' });
    await waitFor(() => expect(connect).toBeEnabled());
    await user.click(connect);
    const picker = await screen.findByTestId('project-integration-connect');
    await user.click(within(picker).getByRole('button', { name: 'Linear' }));
    await user.click(within(picker).getByRole('button', { name: 'Install Linear' }));

    await waitFor(() =>
      expect(startTeamOAuth).toHaveBeenCalledWith(
        expect.objectContaining({ id: 'project-yggdrasil' }),
        'linear',
        {},
      ),
    );
    expect(open).toHaveBeenCalledWith('', 'linear-team-oauth', expect.stringContaining('popup'));
    await waitFor(() => expect(popup.location.href).toContain('linear.example.com'));
    expect(
      await within(picker).findByText('Finish approval in the provider window.'),
    ).toBeInTheDocument();

    // A signal for another install is not this one.
    act(() => {
      window.dispatchEvent(
        storageSignal('niuu:provider-connected', teamInstallSignal('project-yggdrasil', 'slack')),
      );
    });
    expect(screen.getByTestId('project-integration-connect')).toBeInTheDocument();

    act(() => {
      window.dispatchEvent(
        storageSignal('niuu:provider-connected', 'team:project-yggdrasil:linear'),
      );
    });
    await waitFor(() =>
      expect(screen.queryByTestId('project-integration-connect')).not.toBeInTheDocument(),
    );
    expect(await screen.findByText('Linear')).toBeInTheDocument();
  });

  it('opens the provider in a window of its own when the popup was blocked, and asks for config first', async () => {
    const user = userEvent.setup();
    const base = createMockVolundrService();
    const startTeamOAuth = vi.fn(base.startTeamOAuth);
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    renderRealms(PAGE, { volundr: { ...base, startTeamOAuth } });
    await screen.findByTestId(`project-integration-${SLACK}`);
    const connect = screen.getByRole('button', { name: 'Connect' });
    await waitFor(() => expect(connect).toBeEnabled());
    await user.click(connect);
    const picker = await screen.findByTestId('project-integration-connect');
    await user.click(within(picker).getByRole('button', { name: 'Slack' }));
    await user.type(within(picker).getByLabelText('Team channel'), '#ops');
    await user.click(within(picker).getByRole('button', { name: 'Install Slack' }));
    await waitFor(() =>
      expect(startTeamOAuth).toHaveBeenCalledWith(expect.anything(), 'slack', { channel: '#ops' }),
    );
    await waitFor(() =>
      expect(open).toHaveBeenCalledWith(
        expect.stringContaining('slack.example.com'),
        'slack-team-oauth',
        expect.any(String),
      ),
    );
  });

  it('closes the popup and shows the detail when the install cannot start', async () => {
    const user = userEvent.setup();
    const base = createMockVolundrService();
    const popup = { location: { href: '' }, close: vi.fn() };
    vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
    renderRealms(PAGE, {
      volundr: {
        ...base,
        startTeamOAuth: async () => {
          throw Object.assign(new Error('API request failed: 422'), {
            detail: 'No Linear OAuth app is registered on this install',
          });
        },
      },
    });
    await screen.findByTestId(`project-integration-${SLACK}`);
    const connect = screen.getByRole('button', { name: 'Connect' });
    await waitFor(() => expect(connect).toBeEnabled());
    await user.click(connect);
    const picker = await screen.findByTestId('project-integration-connect');
    await user.click(within(picker).getByRole('button', { name: 'Linear' }));
    await user.click(within(picker).getByRole('button', { name: 'Install Linear' }));
    expect(await within(picker).findByRole('alert')).toHaveTextContent(
      'No Linear OAuth app is registered on this install',
    );
    expect(popup.close).toHaveBeenCalled();
    await user.click(within(picker).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByTestId('project-integration-connect')).not.toBeInTheDocument();
  });
});

describe('team integration helpers', () => {
  it('prefers the API detail, then the message, then the value itself', () => {
    expect(errorDetail({ detail: 'because', message: 'API request failed: 422' })).toBe('because');
    expect(errorDetail(new Error('plain'))).toBe('plain');
    expect(errorDetail({ detail: ' ', message: '' })).toBe('[object Object]');
    expect(errorDetail('text')).toBe('text');
  });

  it('knows a 403 from other failures', () => {
    expect(isForbidden({ status: 403 })).toBe(true);
    expect(isForbidden({ status: 500 })).toBe(false);
    expect(isForbidden(undefined)).toBe(false);
  });

  it('finds only an enabled route of that connection and event', () => {
    const route = {
      event: 'errors' as const,
      connection_id: 'c1',
      deliver_to: 'channel' as const,
      channel: '#eng',
      enabled: true,
    };
    expect(routeFor([route], 'c1', 'errors')).toBe(route);
    expect(routeFor([route], 'c2', 'errors')).toBeUndefined();
    expect(routeFor([route], 'c1', 'progress')).toBeUndefined();
    expect(routeFor([{ ...route, enabled: false }], 'c1', 'errors')).toBeUndefined();
  });
});
