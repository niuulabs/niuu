import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import {
  defaultTargetId,
  quickLaunchName,
  quickLaunchSource,
  useQuickLaunch,
  type QuickLaunchOptions,
  type QuickLaunchRequest,
} from './useQuickLaunch';
import { createMockVolundrService } from '../../adapters/mock';
import type { IVolundrService } from '../../ports/IVolundrService';
import type { VolundrTarget } from '../../models/volundr.model';

const navigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}));

function target(id: string, isDefault: boolean): VolundrTarget {
  return {
    id,
    slug: id,
    name: id,
    baseUrl: `http://${id}`,
    enabled: true,
    isDefault,
    tags: [],
  };
}

function Harness({
  request,
  options,
}: {
  request: QuickLaunchRequest;
  options?: QuickLaunchOptions;
}) {
  const { launch, creating, error } = useQuickLaunch();
  return (
    <div>
      <button type="button" onClick={() => void launch(request, options)}>
        launch
      </button>
      <span data-testid="creating">{String(creating)}</span>
      <span data-testid="error">{error ?? ''}</span>
    </div>
  );
}

function renderHarness(
  volundr: IVolundrService,
  request: QuickLaunchRequest,
  options?: QuickLaunchOptions,
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ volundr }}>
        <Harness request={request} options={options} />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

const GIT_REQUEST: QuickLaunchRequest = {
  name: 'volundr',
  source: { type: 'git', repo: 'https://github.com/niuulabs/volundr.git', branch: 'main' },
  definition: {
    key: 'skuldCodex',
    displayName: 'Codex',
    description: '',
    labels: [],
    defaultModel: 'gpt-x',
    compatibleProviders: ['openai'],
  },
  instanceId: 'forge-a',
  initialPrompt: '  fix the tests  ',
  personaName: 'realm-orders',
};

describe('quickLaunchName', () => {
  it('slugifies an explicit name', () => {
    expect(quickLaunchName('My Session', '')).toBe('my-session');
  });

  it('derives the name from the last path segment of the source', () => {
    expect(quickLaunchName('', 'https://github.com/niuulabs/volundr.git')).toBe('volundr');
    expect(quickLaunchName('', '/home/thor/code/niuu/')).toBe('niuu');
  });

  it('falls back to a generic name when there is nothing to derive from', () => {
    expect(quickLaunchName('', '')).toBe('forge-session');
  });
});

describe('quickLaunchSource', () => {
  it('builds a git source', () => {
    expect(quickLaunchSource(false, ' repo.git ', ' main ')).toEqual({
      type: 'git',
      repo: 'repo.git',
      branch: 'main',
    });
  });

  it('builds a local mount that runs in place', () => {
    expect(quickLaunchSource(true, '/work/checkout', '')).toEqual({
      type: 'local_mount',
      local_path: '/work/checkout',
      paths: [{ host_path: '/work/checkout', mount_path: '/workspace', read_only: false }],
    });
  });
});

describe('defaultTargetId', () => {
  it('prefers the default target, then the first', () => {
    expect(defaultTargetId([target('a', false), target('b', true)])).toBe('b');
    expect(defaultTargetId([target('a', false)])).toBe('a');
    expect(defaultTargetId([])).toBeUndefined();
  });
});

describe('useQuickLaunch', () => {
  beforeEach(() => {
    navigate.mockClear();
  });

  it('starts the session, trims the prompt and navigates to it', async () => {
    const volundr = createMockVolundrService();
    const startSession = vi.fn(volundr.startSession);
    volundr.startSession = startSession;
    const onCreated = vi.fn();

    renderHarness(volundr, GIT_REQUEST, { onCreated });
    screen.getByRole('button', { name: 'launch' }).click();

    await waitFor(() => expect(startSession).toHaveBeenCalledTimes(1));
    expect(startSession.mock.calls[0]![0]).toEqual({
      name: 'volundr',
      source: { type: 'git', repo: 'https://github.com/niuulabs/volundr.git', branch: 'main' },
      instanceId: 'forge-a',
      model: 'gpt-x',
      definition: 'skuldCodex',
      taskType: 'skuld-codex',
      initialPrompt: 'fix the tests',
      personaName: 'realm-orders',
      terminalRestricted: false,
      workloadConfig: {},
    });
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
    expect(navigate).toHaveBeenCalledWith({
      to: '/volundr/sessions/$sessionId',
      params: { sessionId: 'sess-new' },
    });
  });

  it('navigates back to the caller when one was given', async () => {
    const volundr = createMockVolundrService();
    renderHarness(volundr, GIT_REQUEST, { returnTo: '/realms/orders' });
    screen.getByRole('button', { name: 'launch' }).click();

    await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: '/realms/orders' }));
  });

  it('surfaces the failure instead of navigating', async () => {
    const volundr = createMockVolundrService();
    volundr.startSession = async () => {
      throw new Error('forge is full');
    };
    renderHarness(volundr, GIT_REQUEST);
    screen.getByRole('button', { name: 'launch' }).click();

    await waitFor(() => expect(screen.getByTestId('error')).toHaveTextContent('forge is full'));
    expect(navigate).not.toHaveBeenCalled();
    expect(screen.getByTestId('creating')).toHaveTextContent('false');
  });
});
