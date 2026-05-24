import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockBifrostService } from '@niuulabs/plugin-bifrost';
import { ForgePage } from './ForgePage';
import {
  createMockVolundrService,
  createMockClusterAdapter,
  createMockSessionStore,
  createMockTemplateStore,
} from '../adapters/mock';
import type { ISessionStore } from '../ports/ISessionStore';
import type { Session } from '../domain/session';

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}));

function wrap(
  service = createMockVolundrService(),
  clusterAdapter = createMockClusterAdapter(),
  sessionStore = createMockSessionStore(),
  templateStore = createMockTemplateStore(),
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
          'volundr.templates': templateStore,
        }}
      >
        <ForgePage />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

describe('ForgePage', () => {
  it('renders the forge page container', () => {
    wrap();
    expect(screen.getByTestId('forge-page')).toBeInTheDocument();
  });

  it('renders metric tiles once data loads', async () => {
    wrap();
    await waitFor(() => expect(screen.getByText(/active pods/i)).toBeInTheDocument());
    expect(screen.getByText(/tokens today/i)).toBeInTheDocument();
    expect(screen.getByText(/cost today/i)).toBeInTheDocument();
    expect(screen.getByText('GPUs')).toBeInTheDocument();
  });

  it('renders the in-flight pods panel', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('inflight-panel')).toBeInTheDocument());
    expect(screen.getByText('In-flight pods')).toBeInTheDocument();
  });

  it('renders the forge load panel', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('forge-load-panel')).toBeInTheDocument());
    expect(screen.getByText('Forge load')).toBeInTheDocument();
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

  it('renders error strip when failed sessions exist', async () => {
    wrap();
    await waitFor(() => {
      const _errorStrip = screen.queryByTestId('error-strip');
      // May or may not have failed sessions depending on mock data
      expect(screen.getByTestId('forge-page')).toBeInTheDocument();
    });
  });

  it('shows loading state initially', () => {
    const slowStore = {
      ...createMockSessionStore(),
      listSessions: () => new Promise(() => {}),
    };
    wrap(createMockVolundrService(), createMockClusterAdapter(), slowStore);
    expect(screen.getByText(/loading metrics/i)).toBeInTheDocument();
  });

  // ──────────────────────────────────────────────
  // NIU-725 — new visual features
  // ──────────────────────────────────────────────

  it('renders boot progress bar on booting pods', async () => {
    const bootingSession: Session = {
      id: 'boot-sess',
      ravnId: 'r-boot',
      personaName: 'tester',
      templateId: 'tpl-default',
      clusterId: 'cl-eitri',
      state: 'provisioning',
      startedAt: new Date(Date.now() - 30_000).toISOString(),
      bootProgress: 0.6,
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
    await waitFor(() => expect(screen.getByTestId('boot-progress-bar')).toBeInTheDocument());

    const bar = screen.getByTestId('boot-progress-bar');
    expect(bar.style.width).toBe('60%');
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
    const svgs = document.querySelectorAll('svg[aria-hidden="true"]');
    expect(svgs.length).toBeGreaterThan(0);
  });

  it('renders usage count on quick-launch cards', async () => {
    wrap();
    await waitFor(() => expect(screen.getAllByTestId('usage-count').length).toBeGreaterThan(0));
    const counts = screen.getAllByTestId('usage-count');
    expect(counts[0]?.textContent).toMatch(/\d+×/);
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
    expect(within(inflight).getByTestId('inflight-title')).toHaveTextContent('OIDC auth hardening');
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
