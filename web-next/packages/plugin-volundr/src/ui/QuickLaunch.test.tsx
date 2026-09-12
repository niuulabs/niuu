import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockBifrostService } from '@niuulabs/plugin-bifrost';
import { QuickLaunch } from './QuickLaunch';
import { FALLBACK_SESSION_DEFINITIONS } from './launchWizardModel';
import { createMockVolundrService } from '../adapters/mock';
import type { IVolundrService } from '../ports/IVolundrService';

const navigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}));

function renderQuickLaunch(volundr: IVolundrService, onOpenChange = () => {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          volundr,
          bifrost: createMockBifrostService(),
          'niuu.repos': { getRepos: volundr.getRepos.bind(volundr), getBranches: async () => [] },
        }}
      >
        <QuickLaunch open onOpenChange={onOpenChange} />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

function mockVolundr(): IVolundrService {
  const volundr = createMockVolundrService();
  volundr.getSessionDefinitions = async () =>
    FALLBACK_SESSION_DEFINITIONS.filter((definition) =>
      ['skuldClaude', 'skuldCodex'].includes(definition.key),
    );
  volundr.getFeatures = async () => ({
    miniMode: true,
    localMountsEnabled: true,
    fileManagerEnabled: true,
  });
  volundr.getTargets = async () => [];
  return volundr;
}

describe('QuickLaunch', () => {
  it.each([true, false])(
    'preserves launch choices in the advanced wizard (mini=%s)',
    async (mini) => {
      const volundr = mockVolundr();
      volundr.getFeatures = async () => ({
        miniMode: mini,
        localMountsEnabled: mini,
        fileManagerEnabled: true,
      });
      volundr.getTargets = async () => [
        {
          id: 'first',
          slug: 'first',
          name: 'First Forge',
          baseUrl: 'https://first.example',
          enabled: true,
          isDefault: true,
          tags: [],
        },
        {
          id: 'second',
          slug: 'second',
          name: 'Second Forge',
          baseUrl: 'https://second.example',
          enabled: true,
          isDefault: false,
          tags: [],
        },
      ];
      renderQuickLaunch(volundr);
      await screen.findByTestId('quick-launch-engine-codex');
      fireEvent.change(screen.getByLabelText('Forge target'), { target: { value: 'second' } });
      await waitFor(() =>
        expect(screen.queryByText('Loading launch options…')).not.toBeInTheDocument(),
      );
      const source = mini ? '/home/test/project' : 'https://github.com/custom/uncatalogued.git';
      fireEvent.change(screen.getByTestId('quick-launch-folder'), { target: { value: source } });
      fireEvent.change(screen.getByTestId('quick-launch-name'), {
        target: { value: 'keep-my-name' },
      });
      fireEvent.change(screen.getByTestId('quick-launch-prompt'), {
        target: { value: 'Keep my instructions' },
      });
      fireEvent.click(screen.getByTestId('quick-launch-engine-codex'));
      if (!mini)
        fireEvent.change(screen.getByLabelText('Branch'), { target: { value: 'feature/custom' } });
      fireEvent.click(screen.getByRole('button', { name: 'Advanced launch' }));
      expect(await screen.findByDisplayValue(source)).toBeInTheDocument();
      expect(screen.getByDisplayValue('keep-my-name')).toBeInTheDocument();
      if (!mini) expect(screen.getByDisplayValue('feature/custom')).toBeInTheDocument();
      fireEvent.click(screen.getByTestId('wizard-next'));
      await waitFor(() => expect(screen.getByTestId('forge-target-select')).toHaveValue('second'));
      expect(screen.getByDisplayValue('Keep my instructions')).toBeInTheDocument();
      fireEvent.click(screen.getByTestId('wizard-next'));
      expect(screen.getByTestId('step-confirm-content')).toHaveTextContent(source);
      expect(screen.getByTestId('step-confirm-content')).toHaveTextContent('Second Forge');
      expect(screen.getByTestId('step-confirm-content')).toHaveTextContent('Codex');
    },
  );
  beforeEach(() => {
    navigate.mockClear();
  });

  it('offers a folder + name + Claude/Codex engines only (no repo/branch, no other engines)', async () => {
    renderQuickLaunch(mockVolundr());
    expect(await screen.findByTestId('quick-launch-engine-codex')).toBeInTheDocument();
    expect(screen.getByTestId('quick-launch-folder')).toBeInTheDocument();
    expect(screen.getByTestId('quick-launch-name')).toBeInTheDocument();
    expect(screen.getByTestId('quick-launch-engine-claude')).toBeInTheDocument();
    expect(screen.getByTestId('quick-launch-engine-codex')).toBeInTheDocument();
    // Only the two supported engines — no Gemini/Aider, and no git repo/branch.
    expect(screen.queryByTestId('quick-launch-engine-gemini')).toBeNull();
    expect(screen.queryByTestId('quick-launch-engine-aider')).toBeNull();
    expect(screen.queryByTestId('quick-launch-repo')).toBeNull();
    expect(screen.queryByTestId('quick-launch-branch')).toBeNull();
  });

  it('Go is disabled until a folder is provided', async () => {
    renderQuickLaunch(mockVolundr());
    await screen.findByTestId('quick-launch-engine-codex');
    await waitFor(() =>
      expect(screen.queryByText('Loading launch options…')).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId('quick-launch-go')).toBeDisabled();
    fireEvent.change(screen.getByTestId('quick-launch-folder'), {
      target: { value: '/home/thor/repos/lexi-frontend' },
    });
    expect(screen.getByTestId('quick-launch-go')).toBeEnabled();
  });

  it('creates a local_mount session from a folder + Codex engine and navigates to it', async () => {
    const volundr = mockVolundr();
    const startSession = vi.fn().mockResolvedValue({ id: 'new-1' });
    (volundr as IVolundrService).startSession = startSession;
    const onOpenChange = vi.fn();

    renderQuickLaunch(volundr, onOpenChange);
    await screen.findByTestId('quick-launch-engine-codex');
    await waitFor(() =>
      expect(screen.queryByText('Loading launch options…')).not.toBeInTheDocument(),
    );

    fireEvent.change(screen.getByTestId('quick-launch-folder'), {
      target: { value: '/home/thor/repos/acme-api' },
    });
    fireEvent.change(screen.getByTestId('quick-launch-name'), { target: { value: 'fix-auth' } });
    fireEvent.click(screen.getByTestId('quick-launch-engine-codex'));
    fireEvent.click(screen.getByTestId('quick-launch-go'));

    await waitFor(() => expect(startSession).toHaveBeenCalledTimes(1));
    expect(startSession).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'fix-auth',
        source: {
          type: 'local_mount',
          local_path: '/home/thor/repos/acme-api',
          paths: [
            { host_path: '/home/thor/repos/acme-api', mount_path: '/workspace', read_only: false },
          ],
        },
        definition: 'skuldCodex',
        model: FALLBACK_SESSION_DEFINITIONS.find((definition) => definition.key === 'skuldCodex')!
          .defaultModel,
      }),
    );
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/volundr/sessions/$sessionId',
        params: { sessionId: 'new-1' },
      }),
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('derives the session name from the folder when name is left blank', async () => {
    const volundr = mockVolundr();
    const startSession = vi.fn().mockResolvedValue({ id: 'new-2' });
    (volundr as IVolundrService).startSession = startSession;

    renderQuickLaunch(volundr);
    await screen.findByTestId('quick-launch-engine-codex');
    await waitFor(() =>
      expect(screen.queryByText('Loading launch options…')).not.toBeInTheDocument(),
    );

    fireEvent.change(screen.getByTestId('quick-launch-folder'), {
      target: { value: '/home/thor/repos/Billing-Service/' },
    });
    fireEvent.click(screen.getByTestId('quick-launch-go'));

    await waitFor(() => expect(startSession).toHaveBeenCalledTimes(1));
    expect(startSession).toHaveBeenCalledWith(expect.objectContaining({ name: 'billing-service' }));
  });

  it('surfaces an error and stays open when create fails', async () => {
    const volundr = mockVolundr();
    (volundr as IVolundrService).startSession = vi.fn().mockRejectedValue(new Error('boom'));

    renderQuickLaunch(volundr);
    await screen.findByTestId('quick-launch-engine-codex');
    await waitFor(() =>
      expect(screen.queryByText('Loading launch options…')).not.toBeInTheDocument(),
    );

    fireEvent.change(screen.getByTestId('quick-launch-folder'), {
      target: { value: '/home/thor/repos/x' },
    });
    fireEvent.click(screen.getByTestId('quick-launch-go'));

    const err = await screen.findByTestId('quick-launch-error');
    expect(err).toHaveTextContent('boom');
    expect(navigate).not.toHaveBeenCalled();
  });
  it('launches a Git repository on the selected full-mode Forge', async () => {
    const volundr = mockVolundr();
    volundr.getFeatures = vi
      .fn()
      .mockResolvedValue({ miniMode: false, localMountsEnabled: false, fileManagerEnabled: true });
    volundr.getTargets = async () => [
      {
        id: 'cluster-a',
        slug: 'a',
        name: 'Cluster A',
        baseUrl: 'https://forge.example',
        enabled: true,
        isDefault: true,
        tags: [],
      },
    ];
    volundr.startSession = vi.fn().mockResolvedValue({ id: 'git-session' });
    renderQuickLaunch(volundr);
    await screen.findByTestId('quick-launch-engine-codex');
    await waitFor(() =>
      expect(screen.queryByText('Loading launch options…')).not.toBeInTheDocument(),
    );
    expect(screen.queryByRole('option', { name: 'Local folder' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByTestId('quick-launch-folder'), {
      target: { value: 'https://github.com/acme/service.git' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: 'Branch' }), { target: { value: 'dev' } });
    fireEvent.click(screen.getByTestId('quick-launch-go'));
    await waitFor(() =>
      expect(volundr.startSession).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'service',
          instanceId: 'cluster-a',
          source: { type: 'git', repo: 'https://github.com/acme/service.git', branch: 'dev' },
        }),
      ),
    );
    expect(volundr.getFeatures).toHaveBeenCalledWith('cluster-a');
  });

  it('keeps launch disabled when capabilities cannot be loaded', async () => {
    const volundr = mockVolundr();
    volundr.getFeatures = vi.fn().mockRejectedValue(new Error('Host unavailable'));
    renderQuickLaunch(volundr);
    expect(await screen.findByRole('alert')).toHaveTextContent('Host unavailable');
    expect(screen.getByTestId('quick-launch-go')).toBeDisabled();
  });

  it('reports an empty engine catalog without fabricating engines', async () => {
    const volundr = mockVolundr();
    volundr.getSessionDefinitions = async () => [];
    renderQuickLaunch(volundr);
    expect(await screen.findByText('No session engines are configured.')).toBeInTheDocument();
    expect(screen.getByTestId('quick-launch-go')).toBeDisabled();
  });
});
