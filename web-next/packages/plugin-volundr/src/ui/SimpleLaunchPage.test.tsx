import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import type { PersonaSummary } from '@niuulabs/domain';
import { SimpleLaunchPage } from './SimpleLaunchPage';
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
    allowedTools: [],
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
  return renderWithVolundr(<SimpleLaunchPage />, {
    service,
    sessionStore: createMockSessionStore(),
    extraServices,
  });
}

describe('SimpleLaunchPage', () => {
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
    await waitFor(() => expect(screen.getByTestId('simple-launch-repo')).toBeInTheDocument());
  });

  it('fetches branches after repository selection and displays the fetched options', async () => {
    const service = createMockVolundrService();
    const repos = (await service.getRepos()).map((repo) => ({ ...repo, branches: [] }));
    const getBranches = vi.fn().mockResolvedValue(['main', 'release']);
    renderPage(service, {
      'niuu.repos': { getRepos: async () => repos, getBranches },
    });
    const select = await screen.findByTestId('simple-launch-repo');
    expect(getBranches).not.toHaveBeenCalled();
    fireEvent.change(select, { target: { value: repos[0]!.cloneUrl } });
    expect(await screen.findByRole('option', { name: 'release' })).toBeInTheDocument();
    expect(getBranches).toHaveBeenCalledExactlyOnceWith(repos[0]!.cloneUrl);
  });

  it('lists what happens next and the most recent sessions', async () => {
    renderPage();
    expect(screen.getByText('What happens next')).toBeInTheDocument();
    const recent = await screen.findByTestId('simple-launch-recent');
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

    expect(screen.getByTestId('simple-launch-task')).toHaveValue('Fix the failing auth tests');
    await waitFor(() =>
      expect(screen.getByTestId('simple-launch-repo')).toHaveValue('github.com/niuulabs/volundr'),
    );
    expect(screen.getByTestId('simple-launch-branch')).toHaveValue('develop');
  });

  it('starts a session with the chosen engine and repository, then opens it', async () => {
    const service = createMockVolundrService();
    const startSession = vi.fn(service.startSession);
    service.startSession = startSession;
    setSearch({ repo: 'github.com/niuulabs/volundr', branch: 'main' });
    renderPage(service);

    fireEvent.change(screen.getByTestId('simple-launch-task'), {
      target: { value: 'Fix the failing auth tests' },
    });
    const codex = await screen.findByTestId('simple-launch-engine-codex');
    fireEvent.click(codex);
    await waitFor(() => expect(screen.getByTestId('simple-launch-start')).toBeEnabled());
    fireEvent.click(screen.getByTestId('simple-launch-start'));

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

    const task = screen.getByTestId('simple-launch-task');
    fireEvent.change(task, { target: { value: 'Ship it' } });
    await waitFor(() => expect(screen.getByTestId('simple-launch-start')).toBeEnabled());
    fireEvent.keyDown(task, { key: 'Enter', metaKey: true });

    await waitFor(() => expect(startSession).toHaveBeenCalledTimes(1));
  });

  it('returns to the caller when a back target was given', async () => {
    setSearch({ repo: 'github.com/niuulabs/volundr', back: '/realms/orders' });
    renderPage();

    fireEvent.change(screen.getByTestId('simple-launch-task'), { target: { value: 'Do it' } });
    await waitFor(() => expect(screen.getByTestId('simple-launch-start')).toBeEnabled());
    fireEvent.click(screen.getByTestId('simple-launch-start'));

    await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: '/realms/orders' }));
  });

  it('keeps the start button disabled until there is a task and a repository', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId('simple-launch-repo')).toBeInTheDocument());
    expect(screen.getByTestId('simple-launch-start')).toBeDisabled();

    fireEvent.change(screen.getByTestId('simple-launch-task'), { target: { value: 'Do it' } });
    expect(screen.getByTestId('simple-launch-start')).toBeDisabled();

    fireEvent.change(screen.getByTestId('simple-launch-repo'), {
      target: { value: 'github.com/niuulabs/volundr' },
    });
    await waitFor(() => expect(screen.getByTestId('simple-launch-start')).toBeEnabled());
  });

  it('surfaces a launch failure instead of navigating', async () => {
    const service = createMockVolundrService();
    service.startSession = async () => {
      throw new Error('forge is full');
    };
    setSearch({ repo: 'github.com/niuulabs/volundr' });
    renderPage(service);

    fireEvent.change(screen.getByTestId('simple-launch-task'), { target: { value: 'Do it' } });
    await waitFor(() => expect(screen.getByTestId('simple-launch-start')).toBeEnabled());
    fireEvent.click(screen.getByTestId('simple-launch-start'));

    await waitFor(() =>
      expect(screen.getByTestId('simple-launch-error')).toHaveTextContent('forge is full'),
    );
    expect(navigate).not.toHaveBeenCalled();
  });

  it('hands the page state to the full wizard under More options', async () => {
    setSearch({ repo: 'github.com/niuulabs/volundr', branch: 'main' });
    renderPage();

    fireEvent.change(screen.getByTestId('simple-launch-task'), { target: { value: 'Do it' } });
    fireEvent.click(await screen.findByTestId('simple-launch-engine-codex'));
    fireEvent.click(screen.getByTestId('simple-launch-more-options'));

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

    const select = await screen.findByTestId('simple-launch-persona');
    expect(select).toHaveValue('muninn');
    expect(screen.getByRole('option', { name: /regin/ })).toBeInTheDocument();
  });

  it('leaves the persona field out when no catalog is registered', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId('simple-launch-repo')).toBeInTheDocument());
    expect(screen.queryByTestId('simple-launch-persona')).not.toBeInTheDocument();
  });
});
