import type {
  ApplyStatus,
  CatalogEntry,
  ConnectIntegrationInput,
  Enrollment,
  IntegrationConnection,
  StackChanges,
  StackSettings,
  StackView,
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
    credentialEnrollment: {
      method: 'claude_setup',
      credentialField: 'token',
      defaultCredentialName: 'claude-code-credentials',
    },
    signInAvailable: true,
  },
  {
    slug: 'codex',
    name: 'OpenAI Codex (ChatGPT)',
    description: 'User-scoped ChatGPT subscription login for Codex runtimes',
    integrationType: 'ai_provider',
    authType: 'device_code',
    credentialSchema: {},
    configSchema: {},
    credentialEnrollment: {
      method: 'codex_device',
      credentialField: 'auth.json',
      defaultCredentialName: 'codex-credentials',
    },
    signInAvailable: true,
  },
  {
    slug: 'grok-build',
    name: 'Grok Build (xAI sign-in)',
    description: 'Sign in with your SuperGrok or X Premium+ account for Grok Build sessions',
    integrationType: 'ai_provider',
    authType: 'device_code',
    credentialSchema: {},
    configSchema: {},
    credentialEnrollment: {
      method: 'grok_device',
      credentialField: 'auth.json',
      defaultCredentialName: 'grok-credentials',
    },
    signInAvailable: true,
  },
  {
    slug: 'xai',
    name: 'xAI (Grok)',
    description: 'xAI API key for Grok models',
    integrationType: 'ai_provider',
    authType: 'api_key',
    credentialSchema: {
      required: ['api_key'],
      properties: { api_key: { label: 'API Key', type: 'password' } },
    },
    configSchema: {},
  },
  {
    slug: 'deepseek',
    name: 'DeepSeek',
    description: 'DeepSeek API key for DeepSeek models and the DeepSeek Harness runtime',
    integrationType: 'ai_provider',
    authType: 'api_key',
    credentialSchema: {
      required: ['api_key'],
      properties: { api_key: { label: 'API Key', type: 'password' } },
    },
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
    credentialEnrollment: {
      method: 'oauth_device',
      credentialField: 'token',
      defaultCredentialName: 'github-signin',
    },
    signInAvailable: true,
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
    bind_host: '0.0.0.0',
    external_host: '192.168.1.42',
    port: 8080,
    skuld_image: 'ghcr.io/niuulabs/skuld:dev',
    checks: [
      { name: 'docker binary', passed: true, warn_only: false, message: 'docker found.' },
      { name: 'docker daemon', passed: true, warn_only: false, message: 'Docker 27.3.1.' },
      {
        name: 'nvidia runtime',
        passed: true,
        warn_only: false,
        message: 'NVIDIA runtime registered.',
      },
      { name: 'gpu', passed: true, warn_only: false, message: 'NVIDIA GB10 · 128 GiB.' },
      {
        name: 'disk space',
        passed: true,
        warn_only: false,
        message: '3 TiB free on /var/lib/niuu.',
      },
      { name: 'port 8080', passed: true, warn_only: false, message: 'Port 8080 is free.' },
      {
        name: 'outbound network',
        passed: true,
        warn_only: false,
        message: 'ghcr.io, huggingface.co, api.anthropic.com, api.openai.com reachable.',
      },
      { name: 'git', passed: true, warn_only: false, message: 'git 2.43.' },
    ],
  },
  checks: [
    {
      name: 'host facts',
      passed: true,
      warnOnly: false,
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
  /** How many status reads an interactive sign-in stays pending before it completes. */
  enrollmentPolls?: number;
  /** Simulate an install without the stack controller (no `niuu up`). */
  stackAvailable?: boolean;
  /** How many status reads an apply stays "applying" before it reports applied. */
  applyPolls?: number;
  initialStack?: Partial<StackSettings>;
}

export const MOCK_MODELS: StackView['models'] = [
  {
    id: 'nemotron-3-nano-30b',
    model: 'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16',
    name: 'NVIDIA Nemotron 3 Nano 30B',
    description: 'Fast agentic coder tuned by NVIDIA. Best default for sessions and residents.',
    weightGib: 62,
    recommended: true,
    fits: true,
    memoryNeededGib: 74,
  },
  {
    id: 'gpt-oss-120b',
    model: 'openai/gpt-oss-120b',
    name: 'OpenAI gpt-oss-120b',
    description: 'Larger reasoning model. Slower per token, stronger on planning.',
    weightGib: 78,
    recommended: false,
    fits: true,
    memoryNeededGib: 90,
  },
  {
    id: 'qwen3-coder-30b',
    model: 'Qwen/Qwen3-Coder-30B-A3B-Instruct',
    name: 'Qwen3-Coder 30B-A3B',
    description: 'Lean coding model with generous headroom for long contexts.',
    weightGib: 24,
    recommended: false,
    fits: true,
    memoryNeededGib: 36,
  },
];

function accessUrlsFor(bindHost: string, externalHost: string, port: number): string[] {
  const urls = [`http://127.0.0.1:${port}`];
  if (bindHost !== '127.0.0.1' && externalHost) urls.push(`http://${externalHost}:${port}`);
  return urls;
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
  const enrollments = new Map<string, Enrollment & { polls: number }>();
  const enrollmentPolls = options.enrollmentPolls ?? 2;
  const stackAvailable = options.stackAvailable ?? true;
  const applyPolls = options.applyPolls ?? 2;
  let stackCurrent: StackSettings = {
    bindHost: '0.0.0.0',
    externalHost: '192.168.1.42',
    port: 8080,
    projectName: 'niuu',
    skuldImage: 'ghcr.io/niuulabs/skuld:dev',
    vllm: {
      enabled: false,
      model: '',
      image: 'nvcr.io/nvidia/vllm:25.09-py3',
      maxModelLen: 65536,
      gpuMemoryUtilization: 0.6,
    },
    accessUrls: [],
    ...options.initialStack,
  };
  let staged: StackChanges = {};
  let apply: { polls: number; changes: Record<string, unknown> } | null = null;
  let sequence = 0;

  const requireStack = () => {
    if (!stackAvailable) {
      throw new Error(
        'Stack changes are not available on this install; start it with `niuu up` (docker mode) to enable them.',
      );
    }
  };

  const withChanges = (base: StackSettings, changes: StackChanges): StackSettings => {
    const bindHost = changes.bind_host ?? base.bindHost;
    const vllm = {
      ...base.vllm,
      enabled: changes.vllm_enabled ?? base.vllm.enabled,
      model: changes.vllm_model ?? base.vllm.model,
      maxModelLen: changes.vllm_max_model_len ?? base.vllm.maxModelLen,
      gpuMemoryUtilization: changes.vllm_gpu_memory_utilization ?? base.vllm.gpuMemoryUtilization,
    };
    return {
      ...base,
      bindHost,
      vllm,
      accessUrls: accessUrlsFor(bindHost, base.externalHost, base.port),
    };
  };

  const stackView = (): StackView => {
    const current = withChanges(stackCurrent, {});
    const stagedNested: Record<string, unknown> = {};
    const docker: Record<string, unknown> = {};
    if (staged.bind_host !== undefined) docker.bind_host = staged.bind_host;
    const vllm: Record<string, unknown> = {};
    if (staged.vllm_enabled !== undefined) vllm.enabled = staged.vllm_enabled;
    if (staged.vllm_model !== undefined) vllm.model = staged.vllm_model;
    if (Object.keys(vllm).length > 0) docker.vllm = vllm;
    if (Object.keys(docker).length > 0) stagedNested.docker = docker;
    return {
      current,
      staged: stagedNested,
      effective: withChanges(stackCurrent, staged),
      models: MOCK_MODELS,
      acceleratorMemoryGib: 128,
      hasStagedChanges: Object.keys(stagedNested).length > 0,
    };
  };

  const addConnection = (
    entry: CatalogEntry,
    credentialName: string,
    config: Record<string, unknown>,
  ): IntegrationConnection => {
    sequence += 1;
    const connection: IntegrationConnection = {
      id: `mock-${sequence}`,
      slug: entry.slug,
      integrationType: entry.integrationType,
      credentialName,
      enabled: true,
      config,
      credentialStatus: 'valid',
      credentialExpiresAt: null,
      credentialErrorCode: null,
    };
    connections.push(connection);
    return connection;
  };

  const publicEnrollment = (row: Enrollment & { polls: number }): Enrollment => {
    const { polls, ...rest } = row;
    void polls;
    return rest;
  };

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
      return addConnection(entry, input.credentialName, input.config);
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
          detail: null,
          repositories: [],
        };
      }
      const sourceControl = connection.integrationType === 'source_control';
      return {
        success: true,
        provider: connection.slug,
        workspace: 'Niuu Labs',
        user: 'you',
        error: null,
        detail: sourceControl ? '2 repositories reachable' : 'Key works · 12 models available',
        repositories: sourceControl ? ['niuulabs/volundr', 'niuulabs/skuld'] : [],
      };
    },
    async startEnrollment(slug, credentialName) {
      await wait();
      const entry = catalog.find((candidate) => candidate.slug === slug);
      if (!entry) throw new Error(`Unknown integration ${slug}`);
      if (!entry.credentialEnrollment) {
        throw new Error('Integration does not support interactive enrollment');
      }
      if (entry.signInAvailable === false) {
        throw new Error(`Sign-in for ${slug} is not configured on this install`);
      }
      const existing = [...enrollments.values()].find(
        (row) =>
          row.providerSlug === slug && (row.state === 'pending' || row.state === 'awaiting_user'),
      );
      if (existing) return publicEnrollment(existing);
      sequence += 1;
      const isBrowser = entry.credentialEnrollment.method === 'claude_setup';
      const row: Enrollment & { polls: number } = {
        id: `enroll-${sequence}`,
        connectionId: `pending-${sequence}`,
        providerSlug: slug,
        credentialName,
        state: 'pending',
        verificationUri: '',
        userCode: '',
        expiresAt: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
        errorCode: '',
        inputRequired: isBrowser,
        polls: 0,
      };
      enrollments.set(row.id, row);
      return publicEnrollment(row);
    },
    async getEnrollment(enrollmentId) {
      await wait();
      const row = enrollments.get(enrollmentId);
      if (!row) throw new Error('Credential enrollment not found');
      if (row.state === 'pending') {
        row.state = 'awaiting_user';
        row.verificationUri = row.inputRequired
          ? 'https://claude.ai/oauth/authorize?client_id=mock'
          : 'https://auth.openai.com/codex/device';
        row.userCode = row.inputRequired ? '' : 'MOCK-1234';
      } else if (row.state === 'awaiting_user' && !row.inputRequired) {
        row.polls += 1;
        if (row.polls >= enrollmentPolls) {
          const entry = catalog.find((candidate) => candidate.slug === row.providerSlug)!;
          row.connectionId = addConnection(entry, row.credentialName, {}).id;
          row.state = 'complete';
        }
      }
      return publicEnrollment(row);
    },
    async cancelEnrollment(enrollmentId) {
      await wait();
      const row = enrollments.get(enrollmentId);
      if (!row) throw new Error('Credential enrollment not found');
      if (row.state === 'pending' || row.state === 'awaiting_user') row.state = 'cancelled';
      return publicEnrollment(row);
    },
    async getStack() {
      await wait();
      requireStack();
      return stackView();
    },
    async stageStack(changes) {
      await wait();
      requireStack();
      if (
        changes.bind_host !== undefined &&
        !['127.0.0.1', '0.0.0.0'].includes(changes.bind_host)
      ) {
        throw new Error(`bind_host must be one of 127.0.0.1, 0.0.0.0; got ${changes.bind_host}`);
      }
      staged = { ...staged, ...changes };
      return stackView();
    },
    async discardStack() {
      await wait();
      requireStack();
      staged = {};
      return stackView();
    },
    async applyStack() {
      await wait();
      requireStack();
      const view = stackView();
      if (!view.hasStagedChanges)
        throw new Error('Nothing is staged; stage a change before applying');
      stackCurrent = withChanges(stackCurrent, staged);
      apply = { polls: 0, changes: view.staged };
      staged = {};
      return {
        state: 'applying',
        startedAt: 'now',
        detail: 'Restarting…',
        changes: apply.changes,
        vllm: null,
      };
    },
    async stackStatus(): Promise<ApplyStatus> {
      await wait();
      requireStack();
      const vllm = stackCurrent.vllm.enabled
        ? {
            state: apply ? 'starting' : 'ready',
            detail: apply ? 'Downloading shards: 40%' : `Serving ${stackCurrent.vllm.model}.`,
          }
        : null;
      if (!apply) return { state: 'idle', startedAt: '', detail: '', changes: {}, vllm };
      apply.polls += 1;
      if (apply.polls < applyPolls) {
        return {
          state: 'applying',
          startedAt: 'now',
          detail: 'Restarting…',
          changes: apply.changes,
          vllm,
        };
      }
      const done = apply.changes;
      apply = null;
      return { state: 'applied', startedAt: 'now', detail: 'Applied.', changes: done, vllm };
    },
    async submitEnrollmentCode(enrollmentId, code) {
      await wait();
      const row = enrollments.get(enrollmentId);
      if (!row) throw new Error('Credential enrollment not found');
      if (!row.inputRequired) throw new Error('This login does not accept an authorization code');
      if (row.state !== 'awaiting_user') throw new Error('Login worker is not running');
      if (!code.trim()) throw new Error('Authorization code is required');
      const entry = catalog.find((candidate) => candidate.slug === row.providerSlug)!;
      row.connectionId = addConnection(entry, row.credentialName, {}).id;
      row.state = 'complete';
      return publicEnrollment(row);
    },
  };
}
