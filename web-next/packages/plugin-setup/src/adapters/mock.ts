import type {
  CatalogEntry,
  ConnectIntegrationInput,
  IntegrationConnection,
  IntegrationTestResult,
  SetupState,
  SystemReport,
} from '../domain/setup';
import type { ISetupService } from '../ports';

export const MOCK_CATALOG: CatalogEntry[] = [
  {
    slug: 'anthropic',
    name: 'Anthropic (Claude API)',
    description: 'Anthropic API key for Claude models',
    integrationType: 'ai_provider',
    authType: 'api_key',
    credentialSchema: {
      required: ['api_key'],
      properties: { api_key: { label: 'API Key', type: 'password' } },
    },
    configSchema: {},
  },
  {
    slug: 'openai',
    name: 'OpenAI',
    description: 'OpenAI API key for GPT/Codex models',
    integrationType: 'ai_provider',
    authType: 'api_key',
    credentialSchema: {
      required: ['api_key'],
      properties: { api_key: { label: 'API Key', type: 'password' } },
    },
    configSchema: {},
  },
  {
    slug: 'claude-code',
    name: 'Claude Code (subscription)',
    description: 'Connect your Claude subscription for Claude Code sessions',
    integrationType: 'ai_provider',
    authType: 'browser_login',
    credentialSchema: {},
    configSchema: {},
  },
  {
    slug: 'github',
    name: 'GitHub',
    description: 'GitHub source control — repo browsing, clone, PRs, and MCP server',
    integrationType: 'source_control',
    authType: 'api_key',
    credentialSchema: {
      required: ['token'],
      properties: { token: { label: 'Personal Access Token', type: 'password' } },
    },
    configSchema: {
      properties: {
        name: { label: 'Display Name', type: 'string' },
        base_url: { label: 'API URL', type: 'url', default: 'https://api.github.com' },
        orgs: { label: 'Organizations', type: 'string[]' },
      },
    },
  },
  {
    slug: 'linear',
    name: 'Linear',
    description: 'Linear issue tracker — issue browsing, status updates, and MCP server',
    integrationType: 'issue_tracker',
    authType: 'api_key',
    credentialSchema: {
      required: ['api_key'],
      properties: { api_key: { label: 'API Key', type: 'password' } },
    },
    configSchema: {},
  },
];

export const MOCK_SYSTEM: SystemReport = {
  host: {
    hostname: 'spark',
    os_name: 'Ubuntu',
    os_version: '24.04',
    arch: 'aarch64',
    cpu_count: 20,
    memory_total_bytes: 128 * 1024 ** 3,
    docker_version: '27.3.1',
    compose_version: '2.40.3',
    nvidia_runtime: true,
    gpus: [{ name: 'NVIDIA GB10', memory_total_mib: 131072, driver_version: '570.86' }],
    data_dir: '/var/lib/niuu',
    disk_free_bytes: 3 * 1024 ** 4,
    disk_total_bytes: 4 * 1024 ** 4,
  },
  checks: [
    {
      name: 'host facts',
      passed: true,
      warnOnly: true,
      message: 'Host facts recorded by niuu up.',
    },
    { name: 'database', passed: true, warnOnly: false, message: 'PostgreSQL reachable.' },
    {
      name: 'docker socket',
      passed: true,
      warnOnly: false,
      message: 'Docker socket available at /var/run/docker.sock.',
    },
    { name: 'git', passed: true, warnOnly: false, message: 'git found: /usr/bin/git' },
  ],
  healthy: true,
};

export interface MockSetupOptions {
  latencyMs?: number;
  initialState?: Partial<SetupState>;
  catalog?: CatalogEntry[];
  system?: SystemReport;
}

export function createMockSetupService(options: MockSetupOptions = {}): ISetupService {
  const latency = options.latencyMs ?? 150;
  const wait = () => new Promise<void>((resolve) => setTimeout(resolve, latency));
  let state: SetupState = {
    enabled: true,
    mode: 'docker',
    completed: false,
    completedAt: null,
    steps: ['welcome', 'system', 'model', 'providers', 'git', 'tracker', 'runtime', 'launch'],
    completedSteps: [],
    ...options.initialState,
  };
  const catalog = options.catalog ?? MOCK_CATALOG;
  const system = options.system ?? MOCK_SYSTEM;
  const connections: IntegrationConnection[] = [];
  let sequence = 0;

  return {
    async getState() {
      await wait();
      return state;
    },
    async getSystem() {
      await wait();
      return system;
    },
    async completeStep(step, data = {}) {
      await wait();
      const record = { step, completedAt: new Date().toISOString(), data };
      state = {
        ...state,
        completedSteps: [...state.completedSteps.filter((r) => r.step !== step), record],
      };
      return state;
    },
    async complete() {
      await wait();
      state = { ...state, completed: true, completedAt: new Date().toISOString() };
      return state;
    },
    async listCatalog() {
      await wait();
      return catalog;
    },
    async listIntegrations() {
      await wait();
      return connections;
    },
    async connectIntegration(input: ConnectIntegrationInput) {
      await wait();
      const entry = catalog.find((candidate) => candidate.slug === input.slug);
      if (!entry) throw new Error(`Unknown integration ${input.slug}`);
      sequence += 1;
      const connection: IntegrationConnection = {
        id: `mock-${sequence}`,
        slug: input.slug,
        integrationType: entry.integrationType,
        credentialName: input.credentialName,
        enabled: true,
        config: input.config,
        credentialStatus: 'valid',
      };
      connections.push(connection);
      return connection;
    },
    async testIntegration(connectionId): Promise<IntegrationTestResult> {
      await wait();
      const connection = connections.find((candidate) => candidate.id === connectionId);
      if (!connection) {
        return {
          success: false,
          provider: 'unknown',
          workspace: null,
          user: null,
          error: 'No such connection',
        };
      }
      return {
        success: true,
        provider: connection.slug,
        workspace: 'Niuu Labs',
        user: 'you',
        error: null,
      };
    },
  };
}
