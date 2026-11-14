import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent, within } from '@testing-library/react';
import type { PersonaSummary } from '@niuulabs/domain';
import { NewSessionPage } from './NewSessionPage';
import { renderWithVolundr } from '../testing/renderWithVolundr';
import { createMockVolundrService, createMockSessionStore } from '../adapters/mock';
import type { IVolundrService } from '../ports/IVolundrService';

const navigate = vi.fn();
const search: Record<string, string> = {};

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
  useSearch: () => search,
  Link: ({
    children,
    to,
    params,
    ...rest
  }: {
    children: React.ReactNode;
    to: string;
    params?: Record<string, string>;
    [key: string]: unknown;
  }) => {
    const href = to.replace(/\$(\w+)/g, (_, key: string) => params?.[key] ?? '');
    return (
      <a href={href} {...rest}>
        {children}
      </a>
    );
  },
}));

vi.mock('./LaunchWizard', () => ({
  LaunchWizard: ({ initialForm }: { initialForm?: Record<string, unknown> }) => (
    <div data-testid="launch-wizard">{JSON.stringify(initialForm)}</div>
  ),
}));

function persona(name: string): PersonaSummary {
  return {
    name,
    role: 'builder',
    letter: name[0]!.toUpperCase(),
    color: '#fff',
    summary: `${name} summary`,
    permissionMode: 'ask',
    iterationBudget: 3,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
  };
}

function setSearch(next: Record<string, string>) {
  for (const key of Object.keys(search)) delete search[key];
  Object.assign(search, next);
}

function renderPage(
  service: IVolundrService = createMockVolundrService(),
  extraServices?: Record<string, unknown>,
) {
  return renderWithVolundr(<NewSessionPage />, {
    service,
    sessionStore: createMockSessionStore(),
    extraServices,
  });
}

describe('NewSessionPage', () => {
  beforeEach(() => {
    navigate.mockClear();
    setSearch({});
  });

  it('asks what the session should work on', async () => {
    renderPage();
    expect(screen.getByRole('heading', { name: 'What should it work on?' })).toBeInTheDocument();
    expect(screen.getByText('new session')).toBeInTheDocument();
    expect(
      screen.getByText('It asks before anything risky; you answer in the chat.'),
    ).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('new-session-repo')).toBeInTheDocument());
  });

  it('fetches branches after repository selection and displays the fetched options', async () => {
    const service = createMockVolundrService();
    const repos = (await service.getRepos()).map((repo) => ({ ...repo, branches: [] }));
    const getBranches = vi.fn().mockResolvedValue(['main', 'release']);
    renderPage(service, {
      'niuu.repos': { getRepos: async () => repos, getBranches },
    });
    const select = await screen.findByTestId('new-session-repo');
    expect(getBranches).not.toHaveBeenCalled();
    fireEvent.change(select, { target: { value: repos[0]!.cloneUrl } });
    expect(await screen.findByRole('option', { name: 'release' })).toBeInTheDocument();
    expect(getBranches).toHaveBeenCalledExactlyOnceWith(repos[0]!.cloneUrl);
  });

  it('lists what happens next and the most recent sessions', async () => {
    renderPage();
    expect(screen.getByText('What happens next')).toBeInTheDocument();
    const recent = await screen.findByTestId('new-session-recent');
    await waitFor(() => expect(recent.querySelectorAll('a').length).toBeGreaterThan(0));
    expect(recent.querySelectorAll('a').length).toBeLessThanOrEqual(3);
  });

  it('prefills the task, repository and branch from the search params', async () => {
    setSearch({
      repo: 'github.com/niuulabs/volundr',
      branch: 'develop',
      prompt: 'Fix the failing auth tests',
    });
    renderPage();

    expect(screen.getByTestId('new-session-task')).toHaveValue('Fix the failing auth tests');
    await waitFor(() =>
      expect(screen.getByTestId('new-session-repo')).toHaveValue('github.com/niuulabs/volundr'),
    );
    expect(screen.getByTestId('new-session-branch')).toHaveValue('develop');
  });

  it("selects the catalog's repository for a link that names it as a session records it", async () => {
    // A side chat's "New chat" passes the session's repository without the catalog's form.
    setSearch({ repo: 'https://github.com/niuulabs/volundr.git/', branch: 'develop' });
    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId('new-session-repo')).toHaveValue('github.com/niuulabs/volundr'),
    );
  });

  it('starts a session with the chosen engine and repository, then opens it', async () => {
    const service = createMockVolundrService();
    const startSession = vi.fn(service.startSession);
    service.startSession = startSession;
    setSearch({ repo: 'github.com/niuulabs/volundr', branch: 'main' });
    renderPage(service);

    fireEvent.change(screen.getByTestId('new-session-task'), {
      target: { value: 'Fix the failing auth tests' },
    });
    const codex = await screen.findByTestId('new-session-engine-codex');
    fireEvent.click(codex);
    await waitFor(() => expect(screen.getByTestId('new-session-start')).toBeEnabled());
    fireEvent.click(screen.getByTestId('new-session-start'));

    await waitFor(() => expect(startSession).toHaveBeenCalledTimes(1));
    const request = startSession.mock.calls[0]![0];
    expect(request.name).toBe('volundr');
    expect(request.source).toEqual({
      type: 'git',
      repo: 'github.com/niuulabs/volundr',
      branch: 'main',
    });
    expect(request.definition).toBe('skuldCodex');
    expect(request.initialPrompt).toBe('Fix the failing auth tests');
    // The session carries the AI account that powers Codex, as every launch does.
    const catalog = await service.getIntegrationCatalog();
    const openaiSlugs = catalog
      .filter((entry) => entry.integrationType === 'ai_provider' && entry.modelVendor === 'openai')
      .map((entry) => entry.slug);
    const openaiIds = (await service.getIntegrations())
      .filter((connection) => openaiSlugs.includes(connection.slug))
      .map((connection) => connection.id);
    expect(openaiIds.length).toBeGreaterThan(0);
    expect(request.integrationIds?.some((id) => openaiIds.includes(id))).toBe(true);
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/volundr/sessions/$sessionId',
        params: { sessionId: 'sess-new' },
      }),
    );
  });

  it('submits on Cmd+Enter from the task box', async () => {
    const service = createMockVolundrService();
    const startSession = vi.fn(service.startSession);
    service.startSession = startSession;
    setSearch({ repo: 'github.com/niuulabs/volundr' });
    renderPage(service);

    const task = screen.getByTestId('new-session-task');
    fireEvent.change(task, { target: { value: 'Ship it' } });
    await waitFor(() => expect(screen.getByTestId('new-session-start')).toBeEnabled());
    fireEvent.keyDown(task, { key: 'Enter', metaKey: true });

    await waitFor(() => expect(startSession).toHaveBeenCalledTimes(1));
  });

  it('returns to the caller when a back target was given', async () => {
    setSearch({ repo: 'github.com/niuulabs/volundr', back: '/realms/orders' });
    renderPage();

    fireEvent.change(screen.getByTestId('new-session-task'), { target: { value: 'Do it' } });
    await waitFor(() => expect(screen.getByTestId('new-session-start')).toBeEnabled());
    fireEvent.click(screen.getByTestId('new-session-start'));

    await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: '/realms/orders' }));
  });

  it('keeps the start button disabled until there is a task and a repository', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId('new-session-repo')).toBeInTheDocument());
    expect(screen.getByTestId('new-session-start')).toBeDisabled();

    fireEvent.change(screen.getByTestId('new-session-task'), { target: { value: 'Do it' } });
    expect(screen.getByTestId('new-session-start')).toBeDisabled();

    fireEvent.change(screen.getByTestId('new-session-repo'), {
      target: { value: 'github.com/niuulabs/volundr' },
    });
    await waitFor(() => expect(screen.getByTestId('new-session-start')).toBeEnabled());
  });

  it('surfaces a launch failure instead of navigating', async () => {
    const service = createMockVolundrService();
    service.startSession = async () => {
      throw new Error('forge is full');
    };
    setSearch({ repo: 'github.com/niuulabs/volundr' });
    renderPage(service);

    fireEvent.change(screen.getByTestId('new-session-task'), { target: { value: 'Do it' } });
    await waitFor(() => expect(screen.getByTestId('new-session-start')).toBeEnabled());
    fireEvent.click(screen.getByTestId('new-session-start'));

    await waitFor(() =>
      expect(screen.getByTestId('new-session-error')).toHaveTextContent('forge is full'),
    );
    expect(navigate).not.toHaveBeenCalled();
  });

  it('hands the page state to the full wizard under More options', async () => {
    setSearch({ repo: 'github.com/niuulabs/volundr', branch: 'main' });
    renderPage();

    fireEvent.change(screen.getByTestId('new-session-task'), { target: { value: 'Do it' } });
    fireEvent.click(await screen.findByTestId('new-session-engine-codex'));
    fireEvent.click(screen.getByTestId('new-session-more-options'));

    const wizard = await screen.findByTestId('launch-wizard');
    const form = JSON.parse(wizard.textContent ?? '{}') as Record<string, string>;
    expect(form).toMatchObject({
      sourcetype: 'git',
      repo: 'github.com/niuulabs/volundr',
      branch: 'main',
      sessionName: 'volundr',
      initialPrompt: 'Do it',
      definition: 'skuldCodex',
    });
  });

  it('offers personas when the catalog is registered, prefilled from the search param', async () => {
    setSearch({ persona: 'muninn' });
    renderPage(createMockVolundrService(), {
      'ravn.personas': { listPersonas: async () => [persona('muninn'), persona('regin')] },
    });

    const select = await screen.findByTestId('new-session-persona');
    expect(select).toHaveValue('muninn');
    expect(screen.getByRole('option', { name: /regin/ })).toBeInTheDocument();
  });

  it('leaves the persona field out when no catalog is registered', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId('new-session-repo')).toBeInTheDocument());
    expect(screen.queryByTestId('new-session-persona')).not.toBeInTheDocument();
  });
});

describe('NewSessionPage — a team', () => {
  beforeEach(() => {
    navigate.mockClear();
    setSearch({ repo: 'github.com/niuulabs/volundr', branch: 'main' });
  });

  function renderTeam(
    service: IVolundrService = createMockVolundrService(),
    extraServices?: Record<string, unknown>,
  ) {
    const view = renderPage(service, extraServices);
    fireEvent.click(screen.getByRole('button', { name: 'A team' }));
    return view;
  }

  it('offers the two ways in and leaves the single-agent page as it was', () => {
    renderPage();
    expect(screen.getByRole('button', { name: 'One agent' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByTestId('new-session-more-options')).toBeInTheDocument();
    expect(screen.queryByTestId('new-team-members')).not.toBeInTheDocument();
    expect(screen.getByText(/A coding agent picks it up in its own sandbox/)).toBeInTheDocument();
  });

  it('shows who is in the team, the room and what happens next', async () => {
    renderTeam();

    expect(
      screen.getByText(/A team of agents picks it up together, in one workspace/),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Optional — without one they wait in the room for your first message'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('new-team-members')).toBeInTheDocument();
    expect(screen.getByTestId('new-team-agent-agent-1')).toBeInTheDocument();
    expect(screen.getByTestId('new-team-agent-agent-2')).toBeInTheDocument();
    expect(screen.getByTestId('new-team-lead-agent-1')).toHaveTextContent('Leads');
    expect(screen.getByTestId('new-team-lead-agent-2')).toHaveTextContent('Make lead');
    expect(screen.queryByTestId('new-session-more-options')).not.toBeInTheDocument();
    expect(screen.queryByRole('radiogroup', { name: 'Who works on it' })).not.toBeInTheDocument();
    expect(screen.getByTestId('new-session-start')).toHaveTextContent('Start 2 sessions');

    // No task yet: nobody is asked who gets it, and the room says they wait.
    expect(screen.queryByTestId('new-team-task-to')).not.toBeInTheDocument();
    const room = screen.getByTestId('new-team-room');
    expect(room).toHaveTextContent('Everyone joins one room');
    expect(room).toHaveTextContent('All 2 agents start and wait');
    expect(
      screen.getByText(
        'One workspace boots with your repository checked out; the 2 agents work in it together.',
      ),
    ).toBeVisible();
    expect(
      screen.getByText('They wait until you or someone in the room sends the first message.'),
    ).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('new-team-engine-agent-1')).toBeEnabled());
  });

  it('starts a team with no task: nobody is given one and the lead is the first agent', async () => {
    const service = createMockVolundrService();
    const startTeam = vi.fn(service.startTeam);
    service.startTeam = startTeam;
    renderTeam(service);

    await waitFor(() => expect(screen.getByTestId('new-session-start')).toBeEnabled());
    fireEvent.click(screen.getByTestId('new-session-start'));

    await waitFor(() => expect(startTeam).toHaveBeenCalledTimes(1));
    const request = startTeam.mock.calls[0]![0];
    expect(request.name).toBe('volundr');
    expect(request.source).toEqual({
      type: 'git',
      repo: 'github.com/niuulabs/volundr',
      branch: 'main',
    });
    expect(request).not.toHaveProperty('task');
    expect(request).not.toHaveProperty('taskTo');
    expect(request.members.map((member) => member.lead)).toEqual([true, false]);
    expect(request.members.every((member) => member.definition === 'skuldClaude')).toBe(true);
    // The team opens on its root, where the room is.
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/volundr/sessions/$sessionId',
        params: { sessionId: 'team-1' },
      }),
    );
  });

  it('asks who gets the task once one is typed, and sends it to the lead by default', async () => {
    const service = createMockVolundrService();
    const startTeam = vi.fn(service.startTeam);
    service.startTeam = startTeam;
    renderTeam(service);

    fireEvent.change(screen.getByTestId('new-session-task'), {
      target: { value: 'Fix the refresh bug' },
    });
    expect(screen.getByTestId('new-team-task-to-lead')).toHaveAttribute('aria-checked', 'true');
    await waitFor(() =>
      expect(screen.getByTestId('new-team-room')).toHaveTextContent('Claude Code gets the task'),
    );
    expect(screen.getByTestId('new-team-room')).toHaveTextContent('hands work to Claude Code');
    expect(screen.getByText('Claude Code reads the task and hands out the work.')).toBeVisible();

    await waitFor(() => expect(screen.getByTestId('new-session-start')).toBeEnabled());
    fireEvent.click(screen.getByTestId('new-session-start'));

    await waitFor(() => expect(startTeam).toHaveBeenCalledTimes(1));
    expect(startTeam.mock.calls[0]![0]).toMatchObject({
      task: 'Fix the refresh bug',
      taskTo: 'lead',
    });
  });

  it('sends the task to everyone when that is chosen', async () => {
    const service = createMockVolundrService();
    const startTeam = vi.fn(service.startTeam);
    service.startTeam = startTeam;
    renderTeam(service);

    fireEvent.change(screen.getByTestId('new-session-task'), { target: { value: 'Review it' } });
    fireEvent.click(screen.getByTestId('new-team-task-to-everyone'));
    expect(screen.getByTestId('new-team-task-to-everyone')).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByTestId('new-team-room')).toHaveTextContent('All 2 agents get the task');
    expect(
      screen.getByText('Each agent reads the task and they agree who does what.'),
    ).toBeVisible();

    await waitFor(() => expect(screen.getByTestId('new-session-start')).toBeEnabled());
    fireEvent.click(screen.getByTestId('new-session-start'));

    await waitFor(() => expect(startTeam).toHaveBeenCalledTimes(1));
    expect(startTeam.mock.calls[0]![0]).toMatchObject({ task: 'Review it', taskTo: 'everyone' });
  });

  it('submits on Cmd+Enter from the task box', async () => {
    const service = createMockVolundrService();
    const startTeam = vi.fn(service.startTeam);
    service.startTeam = startTeam;
    renderTeam(service);

    const task = screen.getByTestId('new-session-task');
    fireEvent.change(task, { target: { value: 'Ship it' } });
    await waitFor(() => expect(screen.getByTestId('new-session-start')).toBeEnabled());
    fireEvent.keyDown(task, { key: 'Enter', metaKey: true });

    await waitFor(() => expect(startTeam).toHaveBeenCalledTimes(1));
  });

  it('lets each agent pick its engine and persona, who leads, and be added or removed', async () => {
    const service = createMockVolundrService();
    const startTeam = vi.fn(service.startTeam);
    service.startTeam = startTeam;
    renderTeam(service, {
      'ravn.personas': { listPersonas: async () => [persona('coder'), persona('reviewer')] },
    });

    const personaSelect = await screen.findByTestId('new-team-persona-agent-2');
    await waitFor(() => expect(within(personaSelect).getByText(/reviewer/)).toBeInTheDocument());
    fireEvent.change(personaSelect, { target: { value: 'reviewer' } });
    fireEvent.change(screen.getByTestId('new-team-engine-agent-2'), {
      target: { value: 'skuldCodex' },
    });
    fireEvent.click(screen.getByTestId('new-team-lead-agent-2'));
    expect(screen.getByTestId('new-team-lead-agent-1')).toHaveTextContent('Make lead');
    expect(screen.getByTestId('new-team-lead-agent-2')).toHaveTextContent('Leads');

    fireEvent.click(screen.getByTestId('new-team-add-agent'));
    expect(screen.getByTestId('new-team-agent-agent-3')).toBeInTheDocument();
    expect(screen.getByTestId('new-session-start')).toHaveTextContent('Start 3 sessions');
    fireEvent.click(screen.getByTestId('new-team-remove-agent-1'));
    expect(screen.queryByTestId('new-team-agent-agent-1')).not.toBeInTheDocument();
    // The next agent added does not reuse a removed row's id.
    fireEvent.click(screen.getByTestId('new-team-add-agent'));
    expect(screen.getByTestId('new-team-agent-agent-4')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('new-team-remove-agent-4'));

    await waitFor(() => expect(screen.getByTestId('new-session-start')).toBeEnabled());
    fireEvent.click(screen.getByTestId('new-session-start'));

    await waitFor(() => expect(startTeam).toHaveBeenCalledTimes(1));
    expect(startTeam.mock.calls[0]![0].members).toEqual([
      expect.objectContaining({
        definition: 'skuldCodex',
        personaName: 'reviewer',
        lead: true,
      }),
      expect.objectContaining({ definition: 'skuldClaude', personaName: '', lead: false }),
    ]);
  });

  it('starts the team on the same Forge a single launch goes to', async () => {
    const service = createMockVolundrService();
    const startTeam = vi.fn(service.startTeam);
    service.startTeam = startTeam;
    renderTeam(service);

    await waitFor(() => expect(screen.getByTestId('new-session-start')).toBeEnabled());
    fireEvent.click(screen.getByTestId('new-session-start'));
    await waitFor(() => expect(startTeam).toHaveBeenCalledTimes(1));
    expect(startTeam.mock.calls[0]![0].instanceId).toBe('mock-volundr-default');
  });

  it('invites a person found on that Forge, shows them in the room and sends their id', async () => {
    const service = createMockVolundrService();
    const startTeam = vi.fn(service.startTeam);
    const search = vi.fn(service.getTeamInviteCandidates);
    service.startTeam = startTeam;
    service.getTeamInviteCandidates = search;
    renderTeam(service);

    await waitFor(() => expect(screen.getByTestId('new-session-start')).toBeEnabled());
    expect(screen.queryByTestId('new-team-invite')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('new-team-add-person'));
    expect(screen.getByTestId('new-team-add-person')).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Find someone in your org'), {
      target: { value: 'ali' },
    });
    fireEvent.click(await screen.findByRole('button', { name: /Alice Lindqvist/ }));

    // The search ran on the Forge the team will start on.
    expect(search).toHaveBeenCalledWith('ali', {
      instanceId: 'mock-volundr-default',
      signal: expect.anything(),
    });
    const row = screen.getByTestId('new-team-person-alice');
    expect(row).toHaveTextContent('Alice Lindqvist');
    expect(row).toHaveTextContent('joins the room · can message anyone');
    expect(screen.queryByTestId('new-team-invite')).not.toBeInTheDocument();
    expect(screen.getByTestId('new-team-room')).toHaveTextContent(
      'You and Alice Lindqvist can talk to everyone',
    );

    fireEvent.click(screen.getByTestId('new-session-start'));
    await waitFor(() => expect(startTeam).toHaveBeenCalledTimes(1));
    expect(startTeam.mock.calls[0]![0].invitees).toEqual(['alice']);
  });

  it('does not offer someone already invited, and lets an invitee be taken out again', async () => {
    renderTeam();
    const invite = async (typed: string, name: RegExp) => {
      fireEvent.click(screen.getByTestId('new-team-add-person'));
      fireEvent.change(screen.getByLabelText('Find someone in your org'), {
        target: { value: typed },
      });
      fireEvent.click(await screen.findByRole('button', { name }));
    };
    await invite('a', /Alice Lindqvist/);

    fireEvent.click(screen.getByTestId('new-team-add-person'));
    fireEvent.change(screen.getByLabelText('Find someone in your org'), {
      target: { value: 'alice' },
    });
    expect(await screen.findByText('Nobody else in your org matches.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByTestId('new-team-invite')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('new-team-remove-person-alice'));
    expect(screen.queryByTestId('new-team-person-alice')).not.toBeInTheDocument();
    expect(screen.getByTestId('new-team-room')).toHaveTextContent('You can talk to everyone');
  });

  it('keeps the last agent: a team has at least one', () => {
    renderTeam();
    fireEvent.click(screen.getByTestId('new-team-remove-agent-2'));
    expect(screen.getByTestId('new-team-remove-agent-1')).toBeDisabled();
    expect(screen.getByTestId('new-session-start')).toHaveTextContent('Start session');
    expect(
      screen.getByText('One workspace boots with your repository checked out for the agent.'),
    ).toBeVisible();
  });

  it('numbers the agents when there is no persona catalog', () => {
    renderTeam();
    expect(screen.queryByTestId('new-team-persona-agent-1')).not.toBeInTheDocument();
    expect(screen.getByText('Agent 1')).toBeInTheDocument();
    expect(screen.getByText('Agent 2')).toBeInTheDocument();
  });

  it('needs a repository to start, and returns to the caller when a back target was given', async () => {
    setSearch({ back: '/realms/orders' });
    renderTeam();
    await waitFor(() => expect(screen.getByTestId('new-session-repo')).toBeInTheDocument());
    expect(screen.getByTestId('new-session-start')).toBeDisabled();

    fireEvent.change(screen.getByTestId('new-session-repo'), {
      target: { value: 'github.com/niuulabs/volundr' },
    });
    await waitFor(() => expect(screen.getByTestId('new-session-start')).toBeEnabled());
    fireEvent.click(screen.getByTestId('new-session-start'));

    await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: '/realms/orders' }));
  });

  it('says no engine can run the team when no provider is connected', async () => {
    const service = createMockVolundrService();
    service.getIntegrations = async () => [];
    renderTeam(service);

    expect(await screen.findByTestId('new-session-no-provider')).toBeInTheDocument();
    expect(screen.getByTestId('new-session-start')).toBeDisabled();
  });

  it('surfaces a failure to start the team instead of navigating', async () => {
    const service = createMockVolundrService();
    service.startTeam = async () => {
      throw new Error('this runtime cannot host the team room');
    };
    renderTeam(service);

    await waitFor(() => expect(screen.getByTestId('new-session-start')).toBeEnabled());
    fireEvent.click(screen.getByTestId('new-session-start'));

    await waitFor(() =>
      expect(screen.getByTestId('new-session-error')).toHaveTextContent(
        'this runtime cannot host the team room',
      ),
    );
    expect(navigate).not.toHaveBeenCalled();
  });

  it('goes back to one agent with the single-agent page intact', () => {
    renderTeam();
    fireEvent.click(screen.getByRole('button', { name: 'One agent' }));
    expect(screen.queryByTestId('new-team-members')).not.toBeInTheDocument();
    expect(screen.getByTestId('new-session-more-options')).toBeInTheDocument();
    expect(screen.getByTestId('new-session-start')).toHaveTextContent('Start session');
  });
});
