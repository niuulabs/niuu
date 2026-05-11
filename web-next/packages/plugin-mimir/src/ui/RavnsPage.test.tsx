import { describe, it, expect } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { RavnsPage } from './RavnsPage';
import { createMimirMockAdapter } from '../adapters/mock';
import type { IRavnWardenService, RavnWardenSummary } from '../application/useRavns';
import type { IMimirService } from '../ports';
import { renderWithMimir } from '../testing/renderWithMimir';

const wrap = renderWithMimir;

function createStaticWardenService(warden: RavnWardenSummary): IRavnWardenService {
  return {
    async listWardens() {
      return [warden];
    },
    async getWarden() {
      return warden;
    },
    async createWarden() {
      return warden;
    },
    subscribeWarden() {
      return () => {};
    },
    async observeWarden() {
      return warden;
    },
    async installWarden() {
      return warden;
    },
    async startWarden() {
      return warden;
    },
    async stopWarden() {
      return warden;
    },
    async uninstallWarden() {
      return warden;
    },
  };
}

describe('RavnsPage', () => {
  it('renders the page title', () => {
    wrap(<RavnsPage />);
    expect(screen.getByRole('heading', { name: /wardens/i })).toBeInTheDocument();
  });

  it('shows loading state initially', () => {
    wrap(<RavnsPage />);
    expect(screen.getByText(/loading wardens/)).toBeInTheDocument();
  });

  it('renders ravn items after load', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
  });

  it('shows each ravn id', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getByText('ravn-fjolnir')).toBeInTheDocument());
    expect(screen.getByText('ravn-skald')).toBeInTheDocument();
  });

  it('shows ravn state labels', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-state').length).toBeGreaterThan(0));
    const states = screen.getAllByTestId('ravn-state');
    const texts = states.map((el) => el.textContent);
    expect(texts).toContain('active');
    expect(texts).toContain('idle');
  });

  it('shows bio text for each ravn card', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-bio').length).toBeGreaterThan(0));
    const bios = screen.getAllByTestId('ravn-bio');
    expect(bios[0]!.textContent).toBeTruthy();
    expect(bios[0]!.textContent).not.toBe('');
  });

  it('shows pages-touched count in metrics row', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    expect(screen.getAllByText(/pages touched/).length).toBeGreaterThan(0);
  });

  it('shows last dream timestamp in metrics row for ravns with dream cycles', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-dream').length).toBeGreaterThan(0));
    const dreamEl = screen.getAllByTestId('ravn-dream')[0]!;
    expect(dreamEl.textContent).toMatch(/last dream/i);
  });

  it('shows "no dream cycles yet" for ravns without dreams', async () => {
    wrap(<RavnsPage />);
    // ravn-galdra and ravn-vor both have lastDream: null
    await waitFor(() => expect(screen.getAllByTestId('ravn-no-dream').length).toBeGreaterThan(0));
  });

  it('shows mount chips for each ravn', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    expect(screen.getAllByText(/local/).length).toBeGreaterThan(0);
  });

  it('clicking a ravn card shows the profile view', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByTestId('ravn-item')[0]!);
    await waitFor(() => expect(screen.getByTestId('ravn-profile')).toBeInTheDocument());
  });

  it('profile view shows a back button that returns to directory', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByTestId('ravn-item')[0]!);
    await waitFor(() => screen.getByTestId('ravn-profile'));
    fireEvent.click(screen.getByRole('button', { name: /back to wardens/i }));
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
  });

  it('profile view shows dream stats for selected ravn', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    // Find a ravn with dream stats (first one from mock has a dream cycle)
    fireEvent.click(screen.getAllByTestId('ravn-item')[0]!);
    await waitFor(() => screen.getByTestId('ravn-profile'));
    expect(screen.getByTestId('ravn-dream')).toBeInTheDocument();
  });

  it('profile view shows expertise section', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByTestId('ravn-item')[0]!);
    await waitFor(() => screen.getByTestId('ravn-profile'));
    expect(screen.getByTestId('ravn-expertise')).toBeInTheDocument();
    expect(screen.getByText(/areas of expertise/i)).toBeInTheDocument();
  });

  it('profile view shows expertise chips from ravn data', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByTestId('ravn-item')[0]!);
    await waitFor(() => screen.getByTestId('ravn-profile'));
    // ravn-fjolnir has expertise: ['infra', 'api', 'arch']
    expect(screen.getByText('infra')).toBeInTheDocument();
    expect(screen.getByText('api')).toBeInTheDocument();
    expect(screen.getByText('arch')).toBeInTheDocument();
  });

  it('profile view shows observed placement details for local wardens', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByLabelText(/warden ravn-fjolnir/i));
    await waitFor(() => screen.getByTestId('ravn-profile'));

    expect(screen.getByTestId('warden-observed-placement')).toHaveTextContent('This Mac');
    expect(screen.getByTestId('warden-observed-placement')).toHaveTextContent(
      'dev.niuu.ravn.warden.ravn-fjolnir',
    );
    expect(screen.getByTestId('warden-observed-placement')).toHaveTextContent(
      '/Users/jozefvaneenbergen/.ravn/wardens/ravn-fjolnir/warden.plist',
    );
  });

  it('profile view shows tools list in hero section', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByTestId('ravn-item')[0]!);
    await waitFor(() => screen.getByTestId('ravn-profile'));
    expect(screen.getByTestId('ravn-tools')).toBeInTheDocument();
    // ravn-fjolnir has tools: ['mimir', 'web', 'file', 'ravn']
    expect(screen.getByText(/tools:.*mimir/i)).toBeInTheDocument();
  });

  it('keyboard Enter on a ravn card opens the profile', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    const card = screen.getAllByTestId('ravn-item')[0]!;
    fireEvent.keyDown(card, { key: 'Enter' });
    await waitFor(() => expect(screen.getByTestId('ravn-profile')).toBeInTheDocument());
  });

  it('profile view shows no-dream message for ravn with no dream cycles', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    // ravn-galdra is the 3rd card and has no dream cycles
    fireEvent.click(screen.getAllByTestId('ravn-item')[2]!);
    await waitFor(() => screen.getByTestId('ravn-profile'));
    expect(screen.getByTestId('ravn-no-dream')).toBeInTheDocument();
  });

  it('profile view shows "no expertise defined" for ravn with empty expertise', async () => {
    const noExpertise: IMimirService = {
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        listRavnBindings: async () => [
          {
            ravnId: 'ravn-test',
            ravnRune: 'ᚱ',
            role: 'index',
            state: 'idle',
            mountNames: ['local'],
            writeMount: 'local',
            lastDream: null,
            bio: 'Test ravn with no expertise',
            pagesTouched: 0,
            expertise: [],
            tools: ['mimir'],
          },
        ],
      },
    };
    wrap(<RavnsPage />, noExpertise);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByTestId('ravn-item')[0]!);
    await waitFor(() => screen.getByTestId('ravn-profile'));
    expect(screen.getByText(/no expertise defined/i)).toBeInTheDocument();
  });

  it('shows error state when service throws', async () => {
    const failing: IMimirService = {
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        listRavnBindings: async () => {
          throw new Error('ravns service down');
        },
      },
    };
    wrap(<RavnsPage />, failing);
    await waitFor(() => expect(screen.getByText('ravns service down')).toBeInTheDocument());
  });

  it('shows empty message when bindings list is empty', async () => {
    const empty: IMimirService = {
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        listRavnBindings: async () => [],
      },
    };
    wrap(<RavnsPage />, empty);
    await waitFor(() => expect(screen.getByText(/no wardens found/i)).toBeInTheDocument());
  });

  it('can create a new warden from the page form', async () => {
    wrap(<RavnsPage />);

    fireEvent.click(screen.getByRole('button', { name: /create warden/i }));
    fireEvent.change(screen.getByLabelText(/warden name/i), {
      target: { value: 'Research Warden' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^create warden$/i }));

    await waitFor(() => expect(screen.getByTestId('ravn-profile')).toBeInTheDocument());
    expect(screen.getByRole('heading', { name: 'Research Warden' })).toBeInTheDocument();
  });

  it('can create a GitOps warden with deployment settings', async () => {
    wrap(<RavnsPage />);

    fireEvent.click(screen.getByRole('button', { name: /create warden/i }));
    fireEvent.change(screen.getByLabelText(/warden name/i), {
      target: { value: 'Cluster Warden' },
    });
    fireEvent.change(screen.getByLabelText(/deployment target/i), {
      target: { value: 'k8s-gitops' },
    });
    fireEvent.change(screen.getByLabelText(/kubernetes namespace/i), {
      target: { value: 'ravn-dev' },
    });
    fireEvent.change(screen.getByLabelText(/gitops repo path/i), {
      target: { value: '/tmp/gitops' },
    });
    fireEvent.change(screen.getByLabelText(/gitops manifests subdir/i), {
      target: { value: 'clusters/dev/wardens' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^create warden$/i }));

    await waitFor(() => expect(screen.getByTestId('ravn-profile')).toBeInTheDocument());
    expect(screen.getByText(/kubernetes \(gitops\)/i)).toBeInTheDocument();
    expect(screen.getByTestId('warden-deployment-config')).toHaveTextContent('/tmp/gitops');
    expect(screen.getByTestId('warden-deployment-config')).toHaveTextContent('ravn-dev');
  });

  it('can install and start an offline warden from the profile view', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));

    fireEvent.click(screen.getAllByTestId('ravn-item')[2]!);
    await waitFor(() => screen.getByTestId('ravn-profile'));

    expect(screen.getByText(/not installed/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /install on this mac/i }));

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /start service/i })).toBeEnabled(),
    );
    expect(screen.getByTestId('warden-service-label')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /start service/i }));
    await waitFor(() => expect(screen.getByTestId('ravn-state')).toHaveTextContent('active'));
  });

  it('shows target-aware lifecycle labels for a GitOps warden', async () => {
    wrap(<RavnsPage />);

    fireEvent.click(screen.getByRole('button', { name: /create warden/i }));
    fireEvent.change(screen.getByLabelText(/warden name/i), {
      target: { value: 'Cluster Warden' },
    });
    fireEvent.change(screen.getByLabelText(/deployment target/i), {
      target: { value: 'k8s-gitops' },
    });
    fireEvent.change(screen.getByLabelText(/gitops repo path/i), {
      target: { value: '/tmp/gitops' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^create warden$/i }));

    await waitFor(() => expect(screen.getByTestId('ravn-profile')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /render gitops bundle/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /set desired scale to 1/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /remove gitops manifest/i })).toBeInTheDocument();
    expect(screen.getByTestId('warden-observed-placement')).toHaveTextContent('/tmp/gitops');
  });

  it('shows observed placement details for seeded GitOps wardens', async () => {
    const gitopsWarden: RavnWardenSummary = {
      id: 'ravn-vor',
      name: 'Ravn Vor',
      persona: 'planning-agent',
      profile: 'adr-compliance',
      deployment: 'k8s-gitops',
      deploymentKwargs: {
        repo_path: '/Users/jozefvaneenbergen/gitops/platform',
        namespace: 'ravn-dev',
        manifests_subdir: 'clusters/dev/wardens',
        auto_commit: true,
      },
      mountNames: ['platform'],
      writeMount: 'platform',
      categoryScope: ['adr', 'compliance'],
      features: {
        wakefulnessEnabled: true,
        dreamCycleEnabled: true,
        threadQueueEnabled: true,
        threadEnricherEnabled: true,
        recapEnabled: true,
        sourceTriggerEnabled: true,
        stalenessTriggerEnabled: true,
      },
      autostart: false,
      createdAt: '2026-04-15T10:25:00Z',
      createdBy: 'operator',
      runtime: {
        state: 'idle',
        pagesTouched: 11,
        lastDream: null,
      },
      supervisor: {
        installed: true,
        serviceLabel: 'dev-niuu-ravn-warden-ravn-vor',
        serviceFile: '/Users/jozefvaneenbergen/gitops/platform/clusters/dev/wardens/ravn-vor.yaml',
        configFile: '/Users/jozefvaneenbergen/.ravn/wardens/ravn-vor/config.yaml',
        startCommand: 'k8s-gitops',
        lastInstallAt: '2026-04-19T02:30:00Z',
      },
      operator: {
        rune: 'ᚹ',
        role: 'build',
        bio: 'Compiles and cross-references ADR documents with implementation state',
        expertise: ['adr', 'compliance'],
        tools: ['mimir', 'file'],
      },
    };

    wrap(<RavnsPage />, createMimirMockAdapter(), {}, createStaticWardenService(gitopsWarden));
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));

    fireEvent.click(screen.getByLabelText(/warden ravn-vor/i));
    await waitFor(() => screen.getByTestId('ravn-profile'));

    expect(screen.getByTestId('warden-observed-placement')).toHaveTextContent('ravn-dev');
    expect(screen.getByTestId('warden-observed-placement')).toHaveTextContent(
      '/Users/jozefvaneenbergen/gitops/platform',
    );
    expect(screen.getByTestId('warden-observed-placement')).toHaveTextContent(
      'clusters/dev/wardens/ravn-vor.yaml',
    );
    expect(screen.getByTestId('warden-observed-placement')).toHaveTextContent('0 replicas');
  });

  it('can stop and uninstall an installed warden from the profile view', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));

    fireEvent.click(screen.getAllByTestId('ravn-item')[0]!);
    await waitFor(() => screen.getByTestId('ravn-profile'));

    expect(screen.getByText(/installed/i)).toBeInTheDocument();
    expect(screen.getByTestId('ravn-state')).toHaveTextContent('active');

    fireEvent.click(screen.getByRole('button', { name: /stop service/i }));
    await waitFor(() => expect(screen.getByTestId('ravn-state')).toHaveTextContent('idle'));

    fireEvent.click(screen.getByRole('button', { name: /remove service/i }));
    await waitFor(() => expect(screen.getByText(/not installed/i)).toBeInTheDocument());
    expect(screen.getByTestId('ravn-state')).toHaveTextContent('offline');
  });
});
