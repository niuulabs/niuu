import { describe, expect, it } from 'vitest';
import type {
  CatalogEntry,
  IntegrationConnection,
  SessionDefinition,
} from '../models/volundr.model';
import {
  availableEngines,
  connectedProviders,
  describeEngineProviders,
  normalizeVendor,
  quickLaunchIntegrationIds,
  selectedEngineProvider,
  sourceControlIdsForRepo,
  withEngineProvider,
  withRepoSourceControl,
} from './launchEngines';

const definition = (
  key: string,
  compatibleProviders: string[],
  displayName = key,
): SessionDefinition => ({
  key,
  displayName,
  description: '',
  labels: [],
  defaultModel: '',
  compatibleProviders,
});

const connection = (
  slug: string,
  overrides: Partial<IntegrationConnection> = {},
): IntegrationConnection => ({
  id: `${slug}-1`,
  slug,
  integrationType: 'ai_provider',
  credentialName: `${slug}-setup`,
  enabled: true,
  credentialStatus: 'active',
  createdAt: '',
  updatedAt: '',
  ...overrides,
});

const CATALOG: CatalogEntry[] = [
  {
    id: 'claude-code',
    slug: 'claude-code',
    name: 'Claude Code (subscription)',
    description: '',
    integrationType: 'ai_provider',
    modelVendor: 'anthropic',
  },
  {
    id: 'anthropic',
    slug: 'anthropic',
    name: 'Anthropic (Claude API)',
    description: '',
    integrationType: 'ai_provider',
    modelVendor: 'anthropic',
  },
  {
    id: 'codex',
    slug: 'codex',
    name: 'OpenAI Codex (ChatGPT)',
    description: '',
    integrationType: 'ai_provider',
    modelVendor: 'openai',
  },
  {
    id: 'github',
    slug: 'github',
    name: 'GitHub',
    description: '',
    integrationType: 'source_control',
    modelVendor: '',
  },
];

const DEFINITIONS = [
  definition('skuldClaude', ['anthropic'], 'Claude Code'),
  definition('skuldCodex', ['openai'], 'Codex'),
  definition('skuldGrok', ['xai'], 'Grok Build'),
  definition('skuldOpenCode', [], 'OpenCode'),
];

describe('normalizeVendor', () => {
  it('folds the runtime aliases onto the vendor names the backend uses', () => {
    expect(normalizeVendor('Claude')).toBe('anthropic');
    expect(normalizeVendor('codex')).toBe('openai');
    expect(normalizeVendor(' xai ')).toBe('xai');
    expect(normalizeVendor(undefined)).toBe('');
  });
});

describe('connectedProviders', () => {
  it('keeps only usable AI-provider connections the catalog knows', () => {
    const providers = connectedProviders(
      [
        connection('claude-code'),
        connection('github'),
        connection('codex', { enabled: false }),
        connection('anthropic', { credentialStatus: 'missing' }),
        connection('anthropic', { id: 'enrolling', credentialStatus: 'enrolling' }),
        connection('mystery'),
      ],
      CATALOG,
    );
    expect(providers.map((provider) => provider.connection.id)).toEqual(['claude-code-1']);
    expect(providers[0]!.vendor).toBe('anthropic');
  });
});

describe('availableEngines', () => {
  it('offers an engine when a connected provider unlocks one of its vendors', () => {
    const engines = availableEngines(DEFINITIONS, [connection('claude-code')], CATALOG);
    expect(engines.map((engine) => engine.definition.key)).toEqual([
      'skuldClaude',
      'skuldOpenCode',
    ]);
  });

  it('offers provider-neutral engines once any AI provider is connected', () => {
    expect(availableEngines(DEFINITIONS, [], CATALOG)).toEqual([]);
    const engines = availableEngines(DEFINITIONS, [connection('codex')], CATALOG);
    expect(engines.map((engine) => engine.definition.key)).toEqual(['skuldCodex', 'skuldOpenCode']);
  });

  it('lists every provider that powers an engine, in connection order', () => {
    const engines = availableEngines(
      DEFINITIONS,
      [connection('anthropic'), connection('claude-code'), connection('codex')],
      CATALOG,
    );
    const claude = engines.find((engine) => engine.definition.key === 'skuldClaude')!;
    expect(claude.providers.map((provider) => provider.connection.slug)).toEqual([
      'anthropic',
      'claude-code',
    ]);
    const neutral = engines.find((engine) => engine.definition.key === 'skuldOpenCode')!;
    expect(neutral.providers).toHaveLength(3);
  });
});

describe('describeEngineProviders', () => {
  it('names the provider and the account, and flags an expired sign-in', () => {
    const [engine] = availableEngines(
      [definition('skuldClaude', ['anthropic'])],
      [
        connection('claude-code'),
        connection('anthropic', { id: 'expired', credentialStatus: 'auth_required' }),
      ],
      CATALOG,
    );
    expect(describeEngineProviders(engine!)).toBe(
      'Claude Code (subscription) · claude-code-setup, Anthropic (Claude API) · anthropic-setup (sign-in expired)',
    );
  });

  it('drops the account when it only repeats the provider name', () => {
    const [engine] = availableEngines(
      [definition('skuldCodex', ['openai'])],
      [connection('codex', { credentialName: 'OpenAI Codex (ChatGPT)' })],
      CATALOG,
    );
    expect(describeEngineProviders(engine!)).toBe('OpenAI Codex (ChatGPT)');
  });
});

describe('hidden engines', () => {
  it('keeps batch and remote-control definitions out of the launch dialogs', () => {
    const batch = { ...definition('skuldCodexExec', ['openai']), labels: ['session', 'batch'] };
    const remote = {
      ...definition('skuldClaudeRemote', ['anthropic']),
      labels: ['session', 'remote-control'],
    };
    const engines = availableEngines(
      [...DEFINITIONS, batch, remote],
      [connection('claude-code'), connection('codex')],
      CATALOG,
    );
    expect(engines.map((engine) => engine.definition.key)).toEqual([
      'skuldClaude',
      'skuldCodex',
      'skuldOpenCode',
    ]);
  });
});

describe('account selection', () => {
  const engines = () =>
    availableEngines(
      [definition('skuldClaude', ['anthropic'], 'Claude Code')],
      [
        connection('anthropic', { id: 'key' }),
        connection('claude-code', { id: 'login' }),
        connection('github', { id: 'git', integrationType: 'source_control' }),
      ],
      CATALOG,
    );

  it('reports the selected account, falling back to the first', () => {
    const [engine] = engines();
    expect(selectedEngineProvider(engine, [])?.connection.id).toBe('key');
    expect(selectedEngineProvider(engine, ['git', 'login'])?.connection.id).toBe('login');
    expect(selectedEngineProvider(undefined, ['login'])).toBeUndefined();
  });

  it('keeps exactly one of the engine accounts in the selection', () => {
    const [engine] = engines();
    expect(withEngineProvider(['git'], engine)).toEqual(['git', 'key']);
    expect(withEngineProvider(['git', 'key'], engine, 'login')).toEqual(['git', 'login']);
    expect(withEngineProvider(['git', 'login'], engine)).toEqual(['git', 'login']);
    expect(withEngineProvider(['git', 'login'], engine, 'nope')).toEqual(['git', 'login']);
    expect(withEngineProvider(['git'], undefined)).toEqual(['git']);
  });
});

describe('source control for a repository', () => {
  const integrations = [
    connection('github', {
      id: 'work',
      credentialName: 'github-work',
      integrationType: 'source_control',
    }),
    connection('github', {
      id: 'home',
      credentialName: 'github-home',
      integrationType: 'source_control',
    }),
    connection('github', {
      id: 'off',
      credentialName: 'github-off',
      integrationType: 'source_control',
      enabled: false,
    }),
    connection('linear', { id: 'tracker', integrationType: 'issue_tracker' }),
    connection('claude-code', { id: 'login' }),
  ];
  const repos = [
    {
      provider: 'github',
      org: 'acme',
      name: 'api',
      cloneUrl: 'https://github.com/acme/api.git',
      defaultBranch: 'main',
      branches: ['main'],
      account: 'github-work',
    },
  ];

  it('clones with the account that listed the repository', () => {
    expect(sourceControlIdsForRepo(integrations, repos, 'https://github.com/acme/api.git')).toEqual(
      ['work'],
    );
  });

  it('offers every enabled account for a pasted URL', () => {
    expect(sourceControlIdsForRepo(integrations, repos, 'https://github.com/x/y.git')).toEqual([
      'work',
      'home',
    ]);
  });

  it('swaps the source-control part of a selection', () => {
    expect(
      withRepoSourceControl(
        ['home', 'login'],
        integrations,
        repos,
        'https://github.com/acme/api.git',
      ),
    ).toEqual(['login', 'work']);
  });

  it('assembles what the quick launch attaches', () => {
    const [engine] = availableEngines(
      [definition('skuldClaude', ['anthropic'])],
      integrations,
      CATALOG,
    );
    expect(
      quickLaunchIntegrationIds({
        provider: engine!.providers[0],
        integrations,
        repos,
        repoUrl: 'https://github.com/acme/api.git',
        local: false,
      }),
    ).toEqual(['login', 'work', 'tracker']);
    expect(
      quickLaunchIntegrationIds({
        provider: engine!.providers[0],
        integrations,
        repos,
        repoUrl: '/home/me/code',
        local: true,
      }),
    ).toEqual(['login', 'tracker']);
  });
});
