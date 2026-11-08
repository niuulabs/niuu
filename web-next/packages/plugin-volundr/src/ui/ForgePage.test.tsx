import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockBifrostService } from '@niuulabs/plugin-bifrost';
import { ForgePage } from './ForgePage';
import {
  createMockVolundrService,
  createMockClusterAdapter,
  createMockSessionStore,
} from '../adapters/mock';
import type { ISessionStore } from '../ports/ISessionStore';
import type { IClusterAdapter } from '../ports/IClusterAdapter';
import type { Cluster } from '../domain/cluster';
import type { Session } from '../domain/session';

const navigate = vi.hoisted(() => vi.fn());
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}));

function wrap(
  service = createMockVolundrService(),
  clusterAdapter = createMockClusterAdapter(),
  sessionStore = createMockSessionStore(),
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const repoCatalog = {
    getRepos: async () => service.getRepos(),
    getBranches: async (repoUrl: string) => {
      const repos = await service.getRepos();
      const repo = repos.find(
        (item) =>
          item.cloneUrl === repoUrl ||
          item.url === repoUrl ||
          `${item.org}/${item.name}` === repoUrl,
      );
      return repo?.branches ?? ['main'];
    },
  };

  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          bifrost: createMockBifrostService(),
          'niuu.repos': repoCatalog,
          volundr: service,
          clusterAdapter,
          sessionStore,
        }}
      >
        <ForgePage />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

describe('ForgePage', () => {
  it('keeps launch available and explains failed metrics with a working retry', async () => {
    const service = createMockVolundrService();
    const getStats = vi.spyOn(service, 'getStats').mockRejectedValue(new Error('Host timed out'));
    wrap(service);
    expect(screen.getByTestId('quick-launch-panel')).toBeInTheDocument();
    expect(await screen.findByText('Host metrics: unavailable')).toBeInTheDocument();
    expect(screen.getByLabelText('Host connections')).toHaveTextContent(
      'Metrics cover loaded hosts',
    );
    getStats.mockRestore();
    fireEvent.click(screen.getByRole('button', { name: 'Retry host connections' }));
    await waitFor(() =>
      expect(screen.queryByLabelText('Host connections')).not.toBeInTheDocument(),
    );
  });
  it('renders the forge page container', () => {
    wrap();
    expect(screen.getByTestId('forge-page')).toBeInTheDocument();
  });

  it('renders metric tiles once data loads', async () => {
    wrap();
    await waitFor(() => expect(screen.getByText(/active pods/i)).toBeInTheDocument());
    expect(screen.getByText(/tokens today/i)).toBeInTheDocument();
    expect(screen.getByText(/cost today/i)).toBeInTheDocument();
    expect(screen.getByText(/sessions today/i)).toBeInTheDocument();
  });

  it('renders the in-flight pods panel', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('inflight-panel')).toBeInTheDocument());
    expect(screen.getByText('In-flight pods')).toBeInTheDocument();
  });

  it('renders the forge load panel', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('forge-load-panel')).toBeInTheDocument());
    expect(screen.getByText('Host load')).toBeInTheDocument();
  });

  it('renders the quick launch panel', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('quick-launch-panel')).toBeInTheDocument());
    expect(screen.getByText('Quick launch')).toBeInTheDocument();
  });

  it('renders cluster load rows with cluster data', async () => {
    wrap();
    await waitFor(() => expect(screen.getAllByText('Eitri').length).toBeGreaterThan(0));
    expect(screen.getAllByTestId('cluster-load-row').length).toBeGreaterThan(0);
  });

  it('renders cluster resource usage values above load meters', async () => {
    const cluster: Cluster = {
      id: 'cl-test',
      realm: 'asgard',
      name: 'Test Host',
      kind: 'gpu',
      status: 'healthy',
      region: 'test',
      capacity: { cpu: 8, memMi: 16_384, gpu: 2 },
      used: { cpu: 0.5, memMi: 2_048, gpu: 1 },
      disk: { usedGi: 0, totalGi: 0, systemGi: 0, podsGi: 0, logsGi: 0 },
      nodes: [],
      pods: [],
      runningSessions: 0,
      queuedProvisions: 0,
    };
    const clusterAdapter: IClusterAdapter = {
      getClusters: async () => [cluster],
      getCluster: async () => cluster,
    };

    wrap(createMockVolundrService(), clusterAdapter);
    await waitFor(() => expect(screen.getByText('Test Host')).toBeInTheDocument());

    expect(screen.getByTestId('cluster-meter-cpu')).toHaveTextContent('500m / 8c');
    expect(screen.getByTestId('cluster-meter-mem')).toHaveTextContent('2Gi / 16Gi');
    expect(screen.getByTestId('cluster-meter-gpu')).toHaveTextContent('1 / 2');
  });

  it('says why a failed session failed, in Forge’s words', async () => {
    const store = createMockSessionStore();
    const [base] = await store.listSessions();
    const failed: Session = {
      ...base!,
      id: 'sess-failed',
      name: 'provider-mismatch-guard',
      state: 'failed',
      events: [],
      error: 'skuldCodex does not accept provider anthropic',
    };
    wrap(createMockVolundrService(), createMockClusterAdapter(), {
      ...store,
      listSessions: async () => [failed],
    });

    const strip = await screen.findByTestId('error-strip');
    expect(within(strip).getByText('provider-mismatch-guard')).toBeInTheDocument();
    expect(within(strip).getByTestId('error-reason')).toHaveTextContent(
      'skuldCodex does not accept provider anthropic',
    );
  });

  it('shows loading state initially', () => {
    const slowStore = {
      ...createMockSessionStore(),
      listSessions: () => new Promise(() => {}),
    };
    wrap(createMockVolundrService(), createMockClusterAdapter(), slowStore);
    expect(screen.getAllByText(/loading sessions/i).length).toBeGreaterThan(0);
    expect(screen.getByTestId('quick-launch-panel')).toBeInTheDocument();
  });

  // ──────────────────────────────────────────────
  // NIU-725 — new visual features
  // ──────────────────────────────────────────────

  it('shows the launch stage a booting pod is on', async () => {
    const bootingSession: Session = {
      id: 'boot-sess',
      ravnId: 'r-boot',
      personaName: 'tester',
      templateId: 'tpl-default',
      clusterId: 'cl-eitri',
      state: 'provisioning',
      startedAt: new Date(Date.now() - 30_000).toISOString(),
      startup: {
        startedAt: new Date(Date.now() - 30_000).toISOString(),
        progressError: null,
        stages: [
          {
            key: 'containers',
            label: 'Starting containers',
            state: 'active',
            detail: 'Pulling image "ghcr.io/niuulabs/skuld:dev"',
            startedAt: new Date(Date.now() - 6_000).toISOString(),
            finishedAt: null,
          },
        ],
      },
      connectionType: 'cli',
      resources: {
        cpuRequest: 1,
        cpuLimit: 2,
        cpuUsed: 0,
        memRequestMi: 512,
        memLimitMi: 1_024,
        memUsedMi: 0,
        gpuCount: 0,
      },
      env: {},
      events: [],
    };

    const store = createMockSessionStore();
    const overriddenStore: ISessionStore = {
      ...store,
      listSessions: async () => [bootingSession],
      subscribe: (cb) => {
        cb([bootingSession]);
        return () => {};
      },
    };

    wrap(createMockVolundrService(), createMockClusterAdapter(), overriddenStore);
    await waitFor(() =>
      expect(screen.getByTestId('session-startup-headline')).toHaveTextContent(
        'Starting containers · Pulling image "ghcr.io/niuulabs/skuld:dev"',
      ),
    );
    expect(screen.queryByTestId('boot-progress-bar')).not.toBeInTheDocument();
  });

  it('renders connection type badge on pod cards', async () => {
    const session: Session = {
      id: 'sess-cli',
      ravnId: 'r-cli',
      personaName: 'dev',
      templateId: 'tpl-default',
      clusterId: 'cl-eitri',
      state: 'running',
      startedAt: new Date(Date.now() - 3_600_000).toISOString(),
      lastActivityAt: new Date(Date.now() - 60_000).toISOString(),
      connectionType: 'ide',
      resources: {
        cpuRequest: 1,
        cpuLimit: 2,
        cpuUsed: 0.3,
        memRequestMi: 512,
        memLimitMi: 1_024,
        memUsedMi: 200,
        gpuCount: 0,
      },
      env: {},
      events: [],
    };

    const store = createMockSessionStore();
    const overriddenStore: ISessionStore = {
      ...store,
      listSessions: async () => [session],
      subscribe: (cb) => {
        cb([session]);
        return () => {};
      },
    };

    wrap(createMockVolundrService(), createMockClusterAdapter(), overriddenStore);
    await waitFor(() => expect(screen.getByTestId('connection-type-badge')).toBeInTheDocument());
    expect(screen.getByText('IDE')).toBeInTheDocument();
  });

  it('surfaces awaiting_input sessions in a "Needs your input" panel', async () => {
    const session: Session = {
      id: 'sess-blocked',
      ravnId: 'r-blocked',
      name: 'fix-auth',
      personaName: 'dev',
      templateId: 'tpl-default',
      clusterId: 'cl-eitri',
      state: 'awaiting_input',
      startedAt: new Date(Date.now() - 3_600_000).toISOString(),
      lastActivityAt: new Date(Date.now() - 5_000).toISOString(),
      preview: 'Which database should we use?',
      resources: {
        cpuRequest: 1,
        cpuLimit: 2,
        cpuUsed: 0.3,
        memRequestMi: 512,
        memLimitMi: 1_024,
        memUsedMi: 200,
        gpuCount: 0,
      },
      env: {},
      events: [],
    };

    const store = createMockSessionStore();
    const overriddenStore: ISessionStore = {
      ...store,
      listSessions: async () => [session],
      subscribe: (cb) => {
        cb([session]);
        return () => {};
      },
    };

    wrap(createMockVolundrService(), createMockClusterAdapter(), overriddenStore);
    await waitFor(() => expect(screen.getByTestId('attention-strip')).toBeInTheDocument());
    const strip = screen.getByTestId('attention-strip');
    expect(within(strip).getByText('Needs your input')).toBeInTheDocument();
    expect(within(strip).getByText('Which database should we use?')).toBeInTheDocument();
    expect(within(strip).getByText('needs you')).toBeInTheDocument();
    fireEvent.click(within(strip).getByTestId('attention-open'));
    expect(navigate).toHaveBeenCalledWith({
      to: '/volundr/sessions/$sessionId',
      params: { sessionId: 'sess-blocked' },
    });
  });

  it('shows a repository preview as the repo and branch, and a GPU by count', async () => {
    const session: Session = {
      id: 'sess-repo',
      ravnId: 'r-repo',
      name: 'tracker-delivery',
      personaName: 'dev',
      templateId: 'tpl-default',
      clusterId: 'cl-valhalla',
      state: 'running',
      startedAt: new Date(Date.now() - 3_600_000).toISOString(),
      lastActivityAt: new Date(Date.now() - 5_000).toISOString(),
      preview: 'https://github.com/niuulabs/niuu.git#regin',
      resources: {
        cpuRequest: 1,
        cpuLimit: 2,
        cpuUsed: 0.3,
        memRequestMi: 512,
        memLimitMi: 1_024,
        memUsedMi: 200,
        gpuCount: 1,
      },
      env: {},
      events: [],
    };
    const store = createMockSessionStore();
    const overriddenStore: ISessionStore = {
      ...store,
      listSessions: async () => [session],
      subscribe: (cb) => {
        cb([session]);
        return () => {};
      },
    };

    wrap(createMockVolundrService(), createMockClusterAdapter(), overriddenStore);
    const preview = await screen.findByTestId('inflight-preview');
    expect(preview).toHaveTextContent('niuu · regin');
    expect(screen.getByTestId('inflight-gpu')).toHaveTextContent('1 gpu');
  });

  it('reports token and cost totals for the UTC day without an invented rate', async () => {
    wrap();
    await waitFor(() => expect(screen.getAllByText('since 00:00 UTC')).toHaveLength(2));
    expect(screen.queryByText(/projected/)).not.toBeInTheDocument();
    expect(screen.queryByText(/5m avg/)).not.toBeInTheDocument();
  });

  it('renders token and cost stats on active pod cards', async () => {
    const session: Session = {
      id: 'sess-stats',
      ravnId: 'r-stats',
      personaName: 'dev',
      templateId: 'tpl-default',
      clusterId: 'cl-eitri',
      state: 'running',
      startedAt: new Date(Date.now() - 3_600_000).toISOString(),
      lastActivityAt: new Date(Date.now() - 60_000).toISOString(),
      tokensIn: 3_000,
      tokensOut: 1_000,
      costCents: 5,
      resources: {
        cpuRequest: 1,
        cpuLimit: 2,
        cpuUsed: 0.3,
        memRequestMi: 512,
        memLimitMi: 1_024,
        memUsedMi: 200,
        gpuCount: 0,
      },
      env: {},
      events: [],
    };

    const store = createMockSessionStore();
    const overriddenStore: ISessionStore = {
      ...store,
      listSessions: async () => [session],
      subscribe: (cb) => {
        cb([session]);
        return () => {};
      },
    };

    wrap(createMockVolundrService(), createMockClusterAdapter(), overriddenStore);
    await waitFor(() => expect(screen.getByTestId('token-stat')).toBeInTheDocument());
    expect(screen.getByTestId('token-stat').textContent).toBe('4.0k');
    expect(screen.getByTestId('cost-stat').textContent).toBe('$0.05');
  });

  it('renders sparklines in KPI tiles when stats include sparklines', async () => {
    wrap();
    await waitFor(() => expect(screen.getByText(/active pods/i)).toBeInTheDocument());
    await waitFor(() =>
      expect(document.querySelectorAll('svg[aria-hidden="true"]').length).toBeGreaterThan(0),
    );
  });

  it('renders catalog launch specs on quick-launch cards', async () => {
    wrap();
    await waitFor(() =>
      expect(screen.getAllByTestId('quick-launch-card').length).toBeGreaterThan(0),
    );
    expect(screen.getByText('Claude')).toBeInTheDocument();
    expect(screen.getAllByText('Codex').length).toBeGreaterThan(0);
    expect(screen.getByText(/from catalog/i)).toBeInTheDocument();
  });

  it('renders preview text on inflight rows', async () => {
    const session: Session = {
      id: 'prev-sess',
      ravnId: 'r-prev',
      personaName: 'chronicler',
      templateId: 'tpl-default',
      clusterId: 'cl-eitri',
      state: 'running',
      startedAt: new Date(Date.now() - 7_200_000).toISOString(),
      lastActivityAt: new Date(Date.now() - 600_000).toISOString(),
      preview: 'Implement the new batch import pipeline for the analytics service',
      resources: {
        cpuRequest: 1,
        cpuLimit: 2,
        cpuUsed: 0.1,
        memRequestMi: 512,
        memLimitMi: 1_024,
        memUsedMi: 100,
        gpuCount: 0,
      },
      env: {},
      events: [],
    };

    const store = createMockSessionStore();
    const overriddenStore: ISessionStore = {
      ...store,
      listSessions: async () => [session],
      subscribe: (cb) => {
        cb([session]);
        return () => {};
      },
    };

    wrap(createMockVolundrService(), createMockClusterAdapter(), overriddenStore);
    await waitFor(() => expect(screen.getByTestId('inflight-preview')).toBeInTheDocument());
    expect(screen.getByTestId('inflight-preview').textContent).toBe(
      'Implement the new batch import pipeline for the analytics service',
    );
  });

  it('prefers session title and tracker issue in forge lists when present', async () => {
    const session: Session = {
      id: 'sess-issue-1',
      title: 'OIDC auth hardening',
      trackerIssue: {
        id: 'issue-900',
        identifier: 'NIU-900',
        title: 'OIDC auth hardening',
        status: 'in_progress',
        url: 'https://linear.app/niuu/issue/NIU-900',
      },
      ravnId: 'r-issue',
      personaName: 'OIDC auth hardening',
      templateId: 'tpl-default',
      clusterId: 'cl-eitri',
      state: 'running',
      startedAt: new Date(Date.now() - 3_600_000).toISOString(),
      lastActivityAt: new Date(Date.now() - 45_000).toISOString(),
      preview: 'validating callback edge cases',
      resources: {
        cpuRequest: 1,
        cpuLimit: 2,
        cpuUsed: 0.6,
        memRequestMi: 512,
        memLimitMi: 1_024,
        memUsedMi: 384,
        gpuCount: 0,
      },
      env: {},
      events: [],
    };

    const store = createMockSessionStore();
    const overriddenStore: ISessionStore = {
      ...store,
      listSessions: async () => [session],
      subscribe: (cb) => {
        cb([session]);
        return () => {};
      },
    };

    wrap(createMockVolundrService(), createMockClusterAdapter(), overriddenStore);
    await waitFor(() => expect(screen.getByTestId('inflight-panel')).toBeInTheDocument());

    const inflight = screen.getByTestId('inflight-panel');
    expect(await within(inflight).findByTestId('inflight-title')).toHaveTextContent(
      'OIDC auth hardening',
    );
    expect(within(inflight).getByTestId('inflight-ticket')).toHaveTextContent('NIU-900');
    expect(within(inflight).queryByText('sess-issue-1')).not.toBeInTheDocument();
    expect(within(inflight).getAllByText('OIDC auth hardening')).toHaveLength(1);

    const recent = screen.getByTestId('recent-panel');
    expect(within(recent).getByTestId('tail-title')).toHaveTextContent('OIDC auth hardening');
    expect(within(recent).getByTestId('tail-ticket')).toHaveTextContent('NIU-900');
    expect(within(recent).queryByText('sess-issue-1')).not.toBeInTheDocument();
  });

  it('falls back to the raw cluster id and requested label for requested sessions', async () => {
    const session: Session = {
      id: 'req-sess',
      ravnId: 'r-req',
      personaName: 'planner',
      templateId: 'tpl-default',
      clusterId: 'cl-unknown',
      state: 'requested',
      startedAt: new Date(Date.now() - 120_000).toISOString(),
      resources: {
        cpuRequest: 1,
        cpuLimit: 0,
        cpuUsed: 0,
        memRequestMi: 512,
        memLimitMi: 0,
        memUsedMi: 0,
        gpuCount: 0,
      },
      env: {},
      events: [{ id: 'evt-1', at: new Date().toISOString(), body: 'Waiting for capacity' }],
    };

    const store = createMockSessionStore();
    const overriddenStore: ISessionStore = {
      ...store,
      listSessions: async () => [session],
      subscribe: (cb) => {
        cb([session]);
        return () => {};
      },
    };

    wrap(createMockVolundrService(), createMockClusterAdapter(), overriddenStore);
    await waitFor(() => expect(screen.getAllByText('req-sess').length).toBeGreaterThan(0));
    expect(screen.getByText('cl-unknown')).toBeInTheDocument();
    expect(screen.getAllByText('requested').length).toBeGreaterThan(0);
  });

  it('falls back to the latest event body when no preview is available', async () => {
    const session: Session = {
      id: 'evt-sess',
      ravnId: 'r-evt',
      personaName: 'planner',
      templateId: 'tpl-default',
      clusterId: 'cl-unknown',
      state: 'running',
      startedAt: new Date(Date.now() - 120_000).toISOString(),
      resources: {
        cpuRequest: 1,
        cpuLimit: 2,
        cpuUsed: 0.1,
        memRequestMi: 512,
        memLimitMi: 1024,
        memUsedMi: 128,
        gpuCount: 0,
      },
      env: {},
      events: [{ id: 'evt-1', at: new Date().toISOString(), body: 'Waiting for capacity' }],
    };

    const store = createMockSessionStore();
    const overriddenStore: ISessionStore = {
      ...store,
      listSessions: async () => [session],
      subscribe: (cb) => {
        cb([session]);
        return () => {};
      },
    };

    wrap(createMockVolundrService(), createMockClusterAdapter(), overriddenStore);
    await waitFor(() => expect(screen.getAllByText('evt-sess').length).toBeGreaterThan(0));
    expect(screen.getByText('cl-unknown')).toBeInTheDocument();
    expect(screen.getAllByText('Waiting for capacity').length).toBeGreaterThan(0);
  });

  it('renders all sessions link in inflight header', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('all-sessions-link')).toBeInTheDocument());
    expect(screen.getByTestId('all-sessions-link').textContent).toContain('all sessions');
  });

  it('renders cluster kind badges', async () => {
    wrap();
    await waitFor(() => expect(screen.getAllByText('Eitri').length).toBeGreaterThan(0));
    expect(screen.getAllByTestId('cluster-kind-badge').length).toBeGreaterThan(0);
  });

  it('renders details link in forge load header', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('cluster-details-link')).toBeInTheDocument());
    expect(screen.getByTestId('cluster-details-link').textContent).toContain('details');
  });
});
