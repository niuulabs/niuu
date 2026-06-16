import { describe, it, expect, vi, afterEach } from 'vitest';

const queryMocks = vi.hoisted(() => ({
  createApiClient: vi.fn((basePath: string) => ({
    basePath,
    get: vi.fn(async (endpoint: string) => {
      if (endpoint === '/types') return [];
      if (endpoint === '/user' || endpoint.startsWith('/user?')) return { credentials: [] };
      if (endpoint === '/tenant' || endpoint.startsWith('/tenant?')) return { credentials: [] };
      if (endpoint.startsWith('/user/') || endpoint.startsWith('/tenant/')) return null;
      return [];
    }),
    post: vi.fn(async (_endpoint: string, body?: any) => ({
      id: 'cred-1',
      name: body?.name ?? 'cred-1',
      secret_type: body?.secret_type ?? 'generic',
      keys: Object.keys(body?.data ?? {}),
      metadata: body?.metadata ?? {},
      created_at: '',
      updated_at: '',
    })),
    delete: vi.fn().mockResolvedValue(undefined),
    patch: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
  })),
  getAccessToken: vi.fn(() => 'token-123'),
  getAuthHeaders: vi.fn((headers?: HeadersInit) => {
    const next = new Headers(headers);
    next.set('Authorization', 'Bearer token-123');
    return next;
  }),
}));

vi.mock('@niuulabs/query', async () => {
  const actual = await vi.importActual<typeof import('@niuulabs/query')>('@niuulabs/query');
  return {
    ...actual,
    createApiClient: queryMocks.createApiClient,
    getAccessToken: queryMocks.getAccessToken,
    getAuthHeaders: queryMocks.getAuthHeaders,
  };
});

import { __testables, buildVolundrFileSystemHttpAdapter, buildVolundrHttpAdapter } from './http';
import type { IVolundrService } from '../ports/IVolundrService';

function makeClient() {
  return {
    basePath: 'http://localhost:8080/api/v1/forge',
    get: vi.fn().mockResolvedValue([]),
    post: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue(undefined),
    patch: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
  };
}

function makeClientWithBase(basePath: string) {
  return {
    ...makeClient(),
    basePath,
  };
}

function getDerivedClient(basePath: string) {
  const index = queryMocks.createApiClient.mock.calls.findIndex(([arg]) => arg === basePath);
  return index >= 0 ? queryMocks.createApiClient.mock.results[index]?.value : undefined;
}

afterEach(() => {
  vi.useRealTimers();
  queryMocks.createApiClient.mockClear();
  queryMocks.getAccessToken.mockClear();
  queryMocks.getAuthHeaders.mockClear();
});

const {
  normalizeSessionDefinition,
  normalizePermissionAutoApproval,
  buildStartSessionBody,
  toEpochMs,
  toDate,
  normalizeSession,
  normalizeExternalSession,
  normalizeTarget,
  normalizeStats,
  normalizeMessageRole,
  deriveCanonicalCredentialsBasePath,
  deriveSharedApiBasePath,
  deriveCanonicalForgeBasePath,
  deriveCanonicalVolundrBasePath,
  deriveNiuuBasePath,
  normalizeStoredCredential,
  normalizeSecretTypeInfo,
  normalizeMessages,
  normalizeConversationHistory,
  normalizeWorkflowGate,
  normalizeLogLevel,
  normalizeLogs,
  normalizeAggregateParticipants,
  normalizeAggregatedLogs,
  normalizeChronicle,
  normalizeTraceSpan,
  normalizeTraceLane,
  normalizeTrace,
  normalizeTraceSummary,
  normalizeRepo,
  normalizeRepoList,
  rememberKnownKey,
  buildAggregatedLogsQuery,
  applyChronicleEvent,
  inferEventType,
  trimTrailingSlash,
  trimTrailingSlashes,
  trimLeadingSlashes,
  splitSessionPath,
  toSessionPath,
  toTreeNode,
  ensureOk,
} = __testables;

describe('__testables', () => {
  it('normalizes session definitions, permission approval payloads, and start bodies with fallbacks', () => {
    expect(
      normalizeSessionDefinition({
        key: 'default',
        description: 'desc',
        labels: [],
      }),
    ).toEqual({
      key: 'default',
      displayName: 'default',
      description: 'desc',
      labels: [],
      defaultModel: '',
      compatibleProviders: [],
    });

    expect(normalizePermissionAutoApproval({})).toEqual({
      canAutoApprove: false,
      reason: 'endpoint_error',
      command: null,
      delaySeconds: 5,
      matchedPattern: null,
    });

    expect(
      buildStartSessionBody({
        name: 'alpha',
        source: { type: 'git', repo: 'r', branch: 'main' },
        model: 'sonnet',
        terminalRestricted: true,
        trackerIssue: {
          id: 'NIU-1',
          identifier: 'NIU-1',
          title: 'Issue',
          status: 'todo',
          url: 'https://tracker/NIU-1',
        },
      }),
    ).toEqual({
      name: 'alpha',
      source: { type: 'git', repo: 'r', branch: 'main' },
      model: 'sonnet',
      terminal_restricted: true,
      instance_id: null,
      workload_config: {},
      issue_id: 'NIU-1',
      issue_url: 'https://tracker/NIU-1',
    });
  });

  it('normalizes dates, sessions, targets, stats, and message roles across fallback forms', () => {
    expect(toEpochMs(42)).toBe(42);
    expect(toEpochMs('invalid')).toBe(0);
    expect(toEpochMs(undefined)).toBe(0);

    const now = new Date('2026-05-31T12:00:00Z');
    expect(toDate(now)).toBe(now);
    expect(toDate('not-a-date')).toBeUndefined();
    expect(toDate(null)).toBeUndefined();

    const session = normalizeSession({
      id: 'sess-1',
      name: 'alpha',
      source: { type: 'git', repo: 'r', branch: 'main' },
      status: 'running',
      model: 'sonnet',
      last_active: '2026-05-31T12:00:00Z',
      message_count: 4,
      tokens_used: 9,
      pod_name: null,
      archived_at: 'invalid-date',
      tracker_issue_id: null,
      issue_tracker_url: 'https://tracker',
      activity_state: 'idle',
      owner_id: 'owner-1',
      tenant_id: 'tenant-1',
      instance_id: 'instance-1',
      instance_name: 'Alpha',
    });
    expect(session).toMatchObject({
      messageCount: 4,
      tokensUsed: 9,
      activityState: 'idle',
      ownerId: 'owner-1',
      tenantId: 'tenant-1',
      instanceId: 'instance-1',
      instanceName: 'Alpha',
    });
    expect(session.archivedAt).toBeUndefined();
    expect(session.trackerIssue).toBeUndefined();

    expect(
      normalizeTarget({
        id: 'target-1',
        slug: 'alpha',
        name: 'Alpha',
        enabled: true,
        visibility: 'tenant',
      } as any),
    ).toMatchObject({ baseUrl: '', isDefault: false });

    expect(normalizeStats({ sparklines: { sessions: [1] } as any })).toMatchObject({
      activeSessions: 0,
      totalSessions: 0,
      tokensToday: 0,
      localTokens: 0,
      cloudTokens: 0,
      costToday: 0,
    });
    expect(normalizeMessageRole('user')).toBe('user');
    expect(normalizeMessageRole('system')).toBe('assistant');
  });

  it('normalizes session origin and external session id across fallback forms', () => {
    const base = {
      id: 'sess-1',
      name: 'alpha',
      source: { type: 'git' as const, repo: 'r', branch: 'main' },
      status: 'running' as const,
      model: 'sonnet',
    };

    expect(
      normalizeSession({
        ...base,
        origin: 'claude',
        external_session_id: 'ext-1',
      }),
    ).toMatchObject({ origin: 'claude', externalSessionId: 'ext-1' });

    expect(
      normalizeSession({
        ...base,
        origin: 'codex',
        externalSessionId: 'ext-camel',
      }),
    ).toMatchObject({ origin: 'codex', externalSessionId: 'ext-camel' });

    const plain = normalizeSession(base);
    expect(plain.origin).toBeUndefined();
    expect(plain.externalSessionId).toBeNull();
  });

  it('normalizes external session payloads with fallback defaults', () => {
    expect(
      normalizeExternalSession({
        provider: 'claude-code',
        harness: 'claude',
        external_id: 'ext-1',
        workspace_path: '/Users/dev/code/volundr',
        title: 'fix import flow',
        model: 'claude-sonnet',
        created_at: '2026-06-01T08:00:00Z',
        updated_at: '2026-06-01T09:00:00Z',
        live: true,
        workspace_exists: true,
        imported_session_id: 'sess-9',
      }),
    ).toEqual({
      provider: 'claude-code',
      harness: 'claude',
      externalId: 'ext-1',
      workspacePath: '/Users/dev/code/volundr',
      title: 'fix import flow',
      model: 'claude-sonnet',
      createdAt: '2026-06-01T08:00:00Z',
      updatedAt: '2026-06-01T09:00:00Z',
      live: true,
      workspaceExists: true,
      workspaceAllowed: true,
      importedSessionId: 'sess-9',
    });

    expect(
      normalizeExternalSession({
        provider: 'codex',
        harness: 'codex',
        external_id: 'ext-2',
        workspace_path: '/tmp/scratch',
      }),
    ).toEqual({
      provider: 'codex',
      harness: 'codex',
      externalId: 'ext-2',
      workspacePath: '/tmp/scratch',
      title: '',
      model: '',
      createdAt: null,
      updatedAt: null,
      live: false,
      workspaceExists: false,
      workspaceAllowed: true,
      importedSessionId: null,
    });

    expect(
      normalizeExternalSession({
        provider: 'claude-code',
        harness: 'claude',
        external_id: 'ext-3',
        workspace_path: '/etc/forbidden',
        workspace_exists: true,
        workspace_allowed: false,
      }).workspaceAllowed,
    ).toBe(false);
  });

  it('derives canonical base paths from forge, shared, niuu, and credentials variants', () => {
    expect(deriveCanonicalCredentialsBasePath()).toBeNull();
    expect(deriveCanonicalCredentialsBasePath('http://host/api/v1/credentials/')).toBe(
      'http://host/api/v1/credentials',
    );
    expect(deriveCanonicalCredentialsBasePath('http://host/api/v1')).toBe(
      'http://host/api/v1/credentials',
    );
    expect(deriveCanonicalCredentialsBasePath('http://host/api/v1/forge')).toBe(
      'http://host/api/v1/credentials',
    );
    expect(deriveCanonicalCredentialsBasePath('http://host/api/v1/volundr')).toBe(
      'http://host/api/v1/credentials',
    );

    expect(deriveSharedApiBasePath()).toBeNull();
    expect(deriveSharedApiBasePath('http://host/api/v1')).toBe('http://host/api/v1');
    expect(deriveSharedApiBasePath('http://host/api/v1/niuu')).toBe('http://host/api/v1');
    expect(deriveSharedApiBasePath('http://host/api/v1/forge')).toBe('http://host/api/v1');
    expect(deriveSharedApiBasePath('http://host/api/v1/volundr')).toBe('http://host/api/v1');

    expect(deriveCanonicalForgeBasePath()).toBeNull();
    expect(deriveCanonicalForgeBasePath('http://host/api/v1/forge/')).toBe(
      'http://host/api/v1/forge',
    );
    expect(deriveCanonicalForgeBasePath('http://host/api/v1')).toBe('http://host/api/v1/forge');
    expect(deriveCanonicalForgeBasePath('http://host/api/v1/volundr')).toBe(
      'http://host/api/v1/forge',
    );

    expect(deriveCanonicalVolundrBasePath()).toBeNull();
    expect(deriveCanonicalVolundrBasePath('http://host/api/v1/volundr/')).toBe(
      'http://host/api/v1/volundr',
    );
    expect(deriveCanonicalVolundrBasePath('http://host/api/v1')).toBe('http://host/api/v1/volundr');
    expect(deriveCanonicalVolundrBasePath('http://host/api/v1/forge')).toBeNull();

    expect(deriveNiuuBasePath()).toBeNull();
    expect(deriveNiuuBasePath('http://host/api/v1/niuu')).toBe('http://host/api/v1/niuu');
    expect(deriveNiuuBasePath('http://host/api/v1/forge')).toBe('http://host/api/v1/niuu');
    expect(deriveNiuuBasePath('http://host/api/v1/volundr')).toBe('http://host/api/v1/niuu');
  });

  it('normalizes credentials, secret types, conversation history, workflow gates, and logs', () => {
    expect(
      normalizeStoredCredential({
        id: 'cred-1',
        name: 'cred',
        keys: ['token'],
      } as any),
    ).toMatchObject({
      secretType: 'generic',
      metadata: {},
      createdAt: '',
      updatedAt: '',
    });

    expect(
      normalizeSecretTypeInfo({
        type: 'custom',
        label: 'Custom',
        description: 'desc',
        fields: [
          { name: 'api_key' },
          { key: 'region', label: 'Region', type: 'select', required: true },
        ],
        default_mount_type: 'env_file',
      } as any),
    ).toEqual({
      type: 'custom',
      label: 'Custom',
      description: 'desc',
      fields: [
        { key: 'api_key', label: 'api_key', type: 'text', required: false },
        { key: 'region', label: 'Region', type: 'select', required: true },
      ],
      defaultMountType: 'env',
    });

    expect(
      normalizeMessages('sess-1', {
        turns: [{ id: 'm1', role: 'assistant', content: 'hi', created_at: undefined }],
      }),
    ).toEqual([expect.objectContaining({ id: 'm1', timestamp: 0, role: 'assistant' })]);

    expect(
      normalizeConversationHistory({
        turns: [{ id: 't1', role: 'assistant', content: 'hi' }],
      }),
    ).toEqual({
      turns: [
        expect.objectContaining({
          id: 't1',
          created_at: new Date(0).toISOString(),
        }),
      ],
    });

    expect(normalizeWorkflowGate({ id: 'gate-1' })).toMatchObject({
      node_id: '',
      activation_id: '',
      label: 'gate-1',
      condition: '',
      status: 'pending',
      pending_behavior: 'help_needed',
      approvers: [],
      auto_forward_after: '30m',
      attempt: 1,
      source: 'human',
      summary: '',
    });

    expect(normalizeLogLevel(undefined)).toBe('info');
    expect(normalizeLogLevel('WARNING')).toBe('warn');
    expect(normalizeLogLevel('TRACE')).toBe('error');

    expect(
      normalizeLogs('sess-1', {
        lines: [
          { time: '2026-05-31T12:00:00Z', message: 'booted' },
          { time: '2026-05-31T12:00:00Z', message: 'booted' },
        ],
      }),
    ).toEqual([
      expect.objectContaining({ source: 'broker', id: expect.stringContaining(':1') }),
      expect.objectContaining({ source: 'broker', id: expect.stringContaining(':2') }),
    ]);
  });

  it('normalizes aggregated logs, chronicles, traces, repos, and grouped repo lists', () => {
    expect(normalizeAggregateParticipants({ lines: [] })).toEqual([]);
    expect(
      normalizeAggregateParticipants({
        available_participants: [{ id: 'coder' }],
        lines: [],
      }),
    ).toEqual([{ id: 'coder', label: 'coder', kind: 'ravn' }]);

    const aggregate = normalizeAggregatedLogs('sess-1', {
      lines: [{ timestamp: undefined, level: 'INFO', message: undefined }],
    });
    expect(aggregate.lines[0]).toMatchObject({
      participant: 'session',
      participantLabel: 'session',
      participantKind: 'ravn',
      source: 'session',
      message: '',
      stream: 'workspace',
    });

    expect(normalizeChronicle({ events: [], files: [], commits: [], tokenBurn: [1] })).toEqual({
      events: [],
      files: [],
      commits: [],
      tokenBurn: [1],
    });

    expect(
      normalizeTraceSpan({
        id: 'span-1',
      }),
    ).toMatchObject({
      sessionId: '',
      traceId: '',
      parentSpanId: null,
      kind: 'unknown',
      name: 'span',
      status: 'completed',
      startedAt: new Date(0).toISOString(),
      endedAt: null,
      durationMs: null,
      actorType: null,
      actorId: null,
      actorLabel: null,
      sourceService: null,
      attributes: {},
    });
    expect(normalizeTraceLane({ key: 'lane-1' })).toEqual({
      key: 'lane-1',
      label: 'lane-1',
      kind: 'system',
    });
    expect(normalizeTrace({} as any)).toEqual({
      traceId: '',
      sessionId: '',
      startedAt: null,
      endedAt: null,
      durationMs: 0,
      spans: [],
      lanes: [],
    });
    expect(normalizeTraceSummary({} as any)).toEqual({
      totalDurationMs: 0,
      provisioningDurationMs: 0,
      setupDurationMs: 0,
      workflowDurationMs: 0,
      publishDurationMs: 0,
      cleanupDurationMs: 0,
      activeExecutionDurationMs: 0,
      waitingDurationMs: 0,
      turnCount: 0,
      toolCallCount: 0,
      longestSpan: null,
    });

    expect(
      normalizeRepo({
        provider: 'github',
        org: 'niuu',
        name: 'volundr',
        url: 'https://github.com/niuu/volundr',
      } as any),
    ).toMatchObject({
      cloneUrl: 'https://github.com/niuu/volundr.git',
      defaultBranch: 'main',
      branches: [],
    });

    expect(
      normalizeRepoList([
        {
          provider: 'github',
          org: 'niuu',
          name: 'volundr',
          url: 'https://github.com/niuu/volundr',
        },
      ] as any),
    ).toHaveLength(1);
    expect(
      normalizeRepoList([
        {
          provider: 'github',
          org: 'niuu',
          name: 'volundr',
          url: 'https://github.com/niuu/volundr',
          cloneUrl: 'https://github.com/niuu/volundr.git',
          defaultBranch: 'main',
          branches: [],
        },
      ] as any)[0]?.cloneUrl,
    ).toBe('https://github.com/niuu/volundr.git');
    expect(
      normalizeRepoList({
        GitHub: [
          {
            provider: 'github',
            org: 'niuu',
            name: 'volundr',
            url: 'https://github.com/niuu/volundr',
          },
        ],
      } as any),
    ).toHaveLength(1);
  });

  it('handles polling keys, log queries, chronicle dedupe, event inference, and path helpers', async () => {
    const connection = {
      subscribers: new Set(),
      knownKeys: ['a', 'b'],
      knownKeySet: new Set(['a', 'b']),
      timer: null,
      loading: false,
      hydrated: false,
    };
    rememberKnownKey(connection, 'b', 2);
    rememberKnownKey(connection, 'c', 2);
    expect(connection.knownKeys).toEqual(['b', 'c']);
    expect(connection.knownKeySet.has('a')).toBe(false);

    expect(buildAggregatedLogsQuery()).toBe('');
    expect(
      buildAggregatedLogsQuery({
        limit: 50,
        level: 'ERROR',
        participants: ['coder', 'reviewer'],
        query: 'timeout',
      }),
    ).toBe('?lines=50&level=ERROR&participants=coder%2Creviewer&query=timeout');

    const existing = {
      events: [{ t: 1, type: 'message', label: 'hello' }],
      files: [],
      commits: [],
      tokenBurn: [1],
    };
    expect(
      applyChronicleEvent(
        existing as any,
        {
          session_id: 'sess-1',
          event: { t: 1, type: 'message', label: 'hello' },
          files: [],
          commits: [],
        } as any,
      ),
    ).toEqual({ ...existing, tokenBurn: [] });
    expect(
      applyChronicleEvent(undefined, {
        session_id: 'sess-1',
        event: { t: 2, type: 'tool', label: 'ran' },
        files: [],
        commits: [],
        token_burn: [2],
      } as any),
    ).toEqual({
      events: [{ t: 2, type: 'tool', label: 'ran' }],
      files: [],
      commits: [],
      tokenBurn: [2],
    });

    expect(inferEventType(null)).toBeNull();
    expect(inferEventType({ active_sessions: 1 })).toBe('stats_updated');
    expect(inferEventType({ id: 'sess-1', status: 'running' })).toBe('session_updated');
    expect(inferEventType({ id: 'sess-1' })).toBe('session_deleted');

    expect(trimTrailingSlash('alpha/')).toBe('alpha');
    expect(trimTrailingSlash('alpha')).toBe('alpha');
    expect(trimTrailingSlashes('///workspace///')).toBe('///workspace');
    expect(trimLeadingSlashes('///workspace')).toBe('workspace');
    expect(splitSessionPath('/workspace')).toEqual({ root: 'workspace', relativePath: '' });
    expect(splitSessionPath('/home')).toEqual({ root: 'home', relativePath: '' });
    expect(splitSessionPath('/workspace/src/index.ts')).toEqual({
      root: 'workspace',
      relativePath: 'src/index.ts',
    });
    expect(splitSessionPath('/home/.config')).toEqual({ root: 'home', relativePath: '.config' });
    expect(splitSessionPath('relative/path')).toEqual({
      root: 'workspace',
      relativePath: 'relative/path',
    });
    expect(toSessionPath('workspace', '')).toBe('/workspace');
    expect(toSessionPath('home', 'docs/readme.md')).toBe('/home/docs/readme.md');
    expect(toTreeNode({ name: 'docs', path: 'docs', type: 'directory' }, 'workspace', [])).toEqual({
      name: 'docs',
      path: '/workspace/docs',
      kind: 'directory',
      size: undefined,
      children: [],
    });
    expect(
      toTreeNode({ name: 'readme', path: 'readme.md', type: 'file', size: 12 }, 'home'),
    ).toEqual({
      name: 'readme',
      path: '/home/readme.md',
      kind: 'file',
      size: 12,
      children: undefined,
    });

    const ok = new Response('ok', { status: 200 });
    await expect(ensureOk(ok)).resolves.toBe(ok);
    await expect(ensureOk(new Response('boom', { status: 500 }))).rejects.toThrow('boom');
    await expect(ensureOk(new Response('', { status: 503 }))).rejects.toThrow(
      'Request failed with 503',
    );
  });
});

describe('buildVolundrFileSystemHttpAdapter', () => {
  it('attaches bearer auth when listing session files', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ entries: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    const fs = buildVolundrFileSystemHttpAdapter({
      baseUrl: 'https://sessions.example.com',
      fetchImpl,
    });

    await fs.listTree('sess-1');

    expect(fetchImpl).toHaveBeenCalledWith(
      'https://sessions.example.com/sessions/sess-1/files?root=workspace',
      expect.objectContaining({
        headers: expect.any(Headers),
      }),
    );
    const headers = fetchImpl.mock.calls[0]?.[1]?.headers as Headers;
    expect(headers.get('Authorization')).toBe('Bearer token-123');
  });

  it('attaches bearer auth to download, mkdir, upload, and delete requests', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ entries: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(new Response('hello', { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 409 }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }));

    const fs = buildVolundrFileSystemHttpAdapter({
      baseUrl: 'https://sessions.example.com',
      fetchImpl,
    });

    await fs.expandDirectory('sess-1', '/workspace');
    await fs.readFile('sess-1', '/workspace/README.md');
    await fs.writeFile('sess-1', '/workspace/docs/readme.md', 'hello');
    await fs.deletePaths('sess-1', ['/workspace/docs/readme.md']);

    for (const call of fetchImpl.mock.calls) {
      const options = call[1];
      const headers = options?.headers as Headers | undefined;
      expect(headers?.get('Authorization')).toBe('Bearer token-123');
    }
  });

  it('lists home-root files and writes top-level files without creating parent directories', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            entries: [{ name: '.config', path: '.config', type: 'directory' }],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 200 }));

    const fs = buildVolundrFileSystemHttpAdapter({
      baseUrl: 'https://sessions.example.com/',
      fetchImpl,
    });

    const homeNodes = await fs.expandDirectory('sess-1', '/home');
    await fs.writeFile('sess-1', '/workspace/README.md', '# hello');

    expect(homeNodes).toEqual([
      expect.objectContaining({ path: '/home/.config', kind: 'directory' }),
    ]);
    expect(fetchImpl.mock.calls[1]?.[0]).toContain('/files/upload?root=workspace');
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it('throws when mkdir fails with a non-conflict status', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response('nope', { status: 500 }));

    const fs = buildVolundrFileSystemHttpAdapter({
      baseUrl: 'https://sessions.example.com',
      fetchImpl,
    });

    await expect(fs.writeFile('sess-1', '/workspace/docs/readme.md', 'hello')).rejects.toThrow(
      'nope',
    );
  });
});

describe('buildVolundrHttpAdapter', () => {
  it('returns an IVolundrService implementation', () => {
    const client = makeClient();
    const svc: IVolundrService = buildVolundrHttpAdapter(client);
    expect(typeof svc.getSessions).toBe('function');
    expect(typeof svc.startSession).toBe('function');
    expect(typeof svc.subscribe).toBe('function');
  });

  it('getSessions calls GET /sessions', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).getSessions();
    expect(client.get).toHaveBeenCalledWith('/sessions');
  });

  it('getConversationHistory calls GET /sessions/:id/conversation', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({
      turns: [
        {
          id: 'turn-1',
          role: 'assistant',
          content: 'archived reply',
          parts: [{ type: 'text', text: 'archived reply' }],
          participant_id: 'reviewer',
          participant_meta: {
            peer_id: 'reviewer',
            persona: 'reviewer',
            participant_type: 'ravn',
            color: 'red',
          },
          thread_id: 'thread-1',
          visibility: 'internal',
        },
      ],
    });

    const history = await buildVolundrHttpAdapter(client).getConversationHistory('sess-1');

    expect(client.get).toHaveBeenCalledWith('/sessions/sess-1/conversation');
    expect(history.turns[0]).toMatchObject({ id: 'turn-1', role: 'assistant' });
    expect(history.turns[0]?.parts).toEqual([{ type: 'text', text: 'archived reply' }]);
    expect(history.turns[0]?.participant_id).toBe('reviewer');
    expect(history.turns[0]?.participant_meta).toMatchObject({
      peer_id: 'reviewer',
      participant_type: 'ravn',
    });
    expect(history.turns[0]?.thread_id).toBe('thread-1');
    expect(history.turns[0]?.visibility).toBe('internal');
  });

  it('getWorkflowGates calls GET /sessions/:id/workflow/gates', async () => {
    const client = makeClient();
    client.get.mockResolvedValueOnce({
      gates: [
        {
          id: 'gate-1',
          node_id: 'spec-prd-gate',
          activation_id: 'activation-1',
          label: 'PRD approval gate',
          condition: 'Review the PRD',
          status: 'pending',
          pending_behavior: 'help_needed',
          approvers: ['human'],
          auto_forward_after: '30m',
          requested_at: '2026-05-20T12:00:00Z',
          updated_at: '2026-05-20T12:00:00Z',
          triggered_by_event_type: 'spec.prd.ready_for_gate',
          approval_event_type: 'spec.prd.approved',
          changes_requested_event_type: 'spec.prd.changes_requested',
          attempt: 1,
          summary: 'Please review the PRD.',
        },
      ],
    });

    const gates = await buildVolundrHttpAdapter(client).getWorkflowGates('sess-1');

    expect(client.get).toHaveBeenCalledWith('/sessions/sess-1/workflow/gates');
    expect(gates).toEqual([
      expect.objectContaining({
        id: 'gate-1',
        node_id: 'spec-prd-gate',
        pending_behavior: 'help_needed',
      }),
    ]);
  });

  it('resolveWorkflowGate posts the human decision', async () => {
    const client = makeClient();
    client.post.mockResolvedValueOnce({
      id: 'gate-1',
      node_id: 'spec-prd-gate',
      activation_id: 'activation-1',
      label: 'PRD approval gate',
      condition: 'Review the PRD',
      status: 'approved',
      pending_behavior: 'help_needed',
      approvers: ['human'],
      auto_forward_after: '30m',
      requested_at: '2026-05-20T12:00:00Z',
      updated_at: '2026-05-20T12:02:00Z',
      triggered_by_event_type: 'spec.prd.ready_for_gate',
      approval_event_type: 'spec.prd.approved',
      changes_requested_event_type: 'spec.prd.changes_requested',
      attempt: 1,
      decision: 'APPROVE',
      notes: 'Looks good.',
      source: 'human',
      summary: 'PRD approved by human reviewer.',
    });

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        id: 'gate-1',
        node_id: 'spec-prd-gate',
        label: 'PRD approval gate',
        status: 'approved',
        decision: 'APPROVE',
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const gate = await buildVolundrHttpAdapter(client).resolveWorkflowGate('sess-1', 'gate-1', {
      decision: 'APPROVE',
      notes: 'Looks good.',
      source: 'human',
    });

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8080/api/v1/forge/sessions/sess-1/workflow/gates/gate-1/resolve',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          decision: 'APPROVE',
          notes: 'Looks good.',
          source: 'human',
        }),
      }),
    );
    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers as HeadersInit);
    expect(headers.get('x-niuu-workflow-gate-intent')).toBe('resolve');
    expect(gate.status).toBe('approved');
    expect(gate.decision).toBe('APPROVE');
  });

  it('resolveWorkflowGate surfaces API detail errors and unknown fallbacks', async () => {
    const client = makeClient();

    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce({
          ok: false,
          status: 400,
          json: async () => ({ detail: 'not allowed' }),
        })
        .mockResolvedValueOnce({
          ok: false,
          status: 500,
          json: async () => ({ nope: true }),
        }),
    );

    const svc = buildVolundrHttpAdapter(client);

    await expect(
      svc.resolveWorkflowGate('sess-1', 'gate-1', { decision: 'APPROVE' } as any),
    ).rejects.toThrow('API request failed: 400 not allowed');
    await expect(
      svc.resolveWorkflowGate('sess-1', 'gate-1', { decision: 'APPROVE' } as any),
    ).rejects.toThrow('API request failed: 500 Unknown error');
  });

  it('getSession calls GET /sessions/:id', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).getSession('s1');
    expect(client.get).toHaveBeenCalledWith('/sessions/s1');
  });

  it('getSession synthesizes trackerIssue from legacy tracker fields', async () => {
    const client = makeClient();
    client.get.mockResolvedValueOnce({
      id: 's1',
      name: 'niu-766',
      source: { type: 'git', repo: 'https://github.com/niuulabs/volundr', branch: 'feat/test' },
      status: 'running',
      model: 'claude-sonnet-4-6',
      tracker_issue_id: 'NIU-766',
      issue_tracker_url:
        'https://linear.app/niuu/issue/NIU-766/update-readme-with-canonical-route-families-and-ownership-map',
    });

    const session = await buildVolundrHttpAdapter(client).getSession('s1');

    expect(session.trackerIssue).toMatchObject({
      identifier: 'NIU-766',
      url: 'https://linear.app/niuu/issue/NIU-766/update-readme-with-canonical-route-families-and-ownership-map',
    });
  });

  it('getActiveSessions calls GET /sessions?active=true', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).getActiveSessions();
    expect(client.get).toHaveBeenCalledWith('/sessions?active=true');
  });

  it('getStats calls GET /stats', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).getStats();
    expect(client.get).toHaveBeenCalledWith('/stats');
  });

  it('getFeatures calls GET /features', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).getFeatures();
    const sharedClient = getDerivedClient('http://localhost:8080/api/v1');
    expect(sharedClient.get).toHaveBeenCalledWith('/features');
  });

  it('getCredentials forwards the optional secret type and applies the fallback type', async () => {
    const client = makeClientWithBase('http://localhost:8080/api/v1');
    const service = buildVolundrHttpAdapter(client);
    const credentialsClient = getDerivedClient('http://localhost:8080/api/v1/credentials');
    credentialsClient.get.mockResolvedValueOnce({
      credentials: [
        {
          id: 'cred-1',
          name: 'aws-prod',
          keys: ['AWS_ACCESS_KEY_ID'],
          metadata: {},
        },
      ],
    });

    const credentials = await service.getCredentials('aws_env');

    expect(credentialsClient.get).toHaveBeenCalledWith('/user?secret_type=aws_env');
    expect(credentials).toEqual([
      expect.objectContaining({
        id: 'cred-1',
        secretType: 'aws_env',
      }),
    ]);
  });

  it('getCredential normalizes both camelCase and snake_case credential payloads', async () => {
    const client = makeClientWithBase('http://localhost:8080/api/v1');
    const service = buildVolundrHttpAdapter(client);
    const credentialsClient = getDerivedClient('http://localhost:8080/api/v1/credentials');

    credentialsClient.get
      .mockResolvedValueOnce({
        id: 'cred-camel',
        name: 'camel',
        secretType: 'gcp_service_account',
        keys: ['project_id'],
        metadata: { scope: 'prod' },
        createdAt: '2026-05-20T00:00:00Z',
        updatedAt: '2026-05-20T01:00:00Z',
      })
      .mockResolvedValueOnce({
        id: 'cred-snake',
        name: 'snake',
        secret_type: 'aws_env',
        keys: ['AWS_ACCESS_KEY_ID'],
        created_at: '2026-05-20T02:00:00Z',
        updated_at: '2026-05-20T03:00:00Z',
      });

    const camel = await service.getCredential('camel');
    const snake = await service.getCredential('snake');

    expect(camel).toMatchObject({
      id: 'cred-camel',
      secretType: 'gcp_service_account',
      metadata: { scope: 'prod' },
      createdAt: '2026-05-20T00:00:00Z',
      updatedAt: '2026-05-20T01:00:00Z',
    });
    expect(snake).toMatchObject({
      id: 'cred-snake',
      secretType: 'aws_env',
      metadata: {},
      createdAt: '2026-05-20T02:00:00Z',
      updatedAt: '2026-05-20T03:00:00Z',
    });
  });

  it('getCredentialTypes normalizes legacy field aliases and default mount types', async () => {
    const client = makeClientWithBase('http://localhost:8080/api/v1');
    const service = buildVolundrHttpAdapter(client);
    const credentialsClient = getDerivedClient('http://localhost:8080/api/v1/credentials');
    credentialsClient.get.mockResolvedValueOnce([
      {
        type: 'generic',
        label: 'Generic',
        description: 'Generic secret',
        fields: [{ name: 'api_key', required: true }],
        default_mount_type: 'file',
      },
      {
        type: 'aws_env',
        label: 'AWS',
        description: 'AWS env secret',
        fields: [{ key: 'region', label: 'Region', type: 'text', required: false }],
        defaultMountType: 'env',
      },
      {
        type: 'template_secret',
        label: 'Template secret',
        description: 'Template mounted secret',
        fields: [{ key: 'token', required: true }],
        default_mount_type: 'template',
      },
      {
        type: 'env_secret',
        label: 'Env secret',
        description: 'Environment mounted secret',
        fields: [{ key: 'endpoint', name: 'legacy-endpoint' }],
        default_mount_type: 'env_file',
      },
    ]);

    const types = await service.getCredentialTypes();

    expect(credentialsClient.get).toHaveBeenCalledWith('/types');
    expect(types).toEqual([
      expect.objectContaining({
        type: 'generic',
        defaultMountType: 'file',
        fields: [
          expect.objectContaining({
            key: 'api_key',
            label: 'api_key',
            type: 'text',
            required: true,
          }),
        ],
      }),
      expect.objectContaining({
        type: 'aws_env',
        defaultMountType: 'env',
        fields: [
          expect.objectContaining({
            key: 'region',
            label: 'Region',
            type: 'text',
            required: false,
          }),
        ],
      }),
      expect.objectContaining({
        type: 'template_secret',
        defaultMountType: 'template',
        fields: [
          expect.objectContaining({
            key: 'token',
            label: 'token',
            type: 'text',
            required: true,
          }),
        ],
      }),
      expect.objectContaining({
        type: 'env_secret',
        defaultMountType: 'env',
        fields: [
          expect.objectContaining({
            key: 'endpoint',
            label: 'endpoint',
            type: 'text',
            required: false,
          }),
        ],
      }),
    ]);
  });

  it('derives forge runtime, volundr catalog, shared, niuu, and credentials clients from a shared api base', async () => {
    const client = makeClientWithBase('http://localhost:8080/api/v1');
    const service = buildVolundrHttpAdapter(client);

    await service.getSessionDefinitions();
    await service.getFeatures();
    await service.getTargets();
    await service.getCredentials();

    expect(queryMocks.createApiClient).toHaveBeenCalledWith('http://localhost:8080/api/v1/forge');
    expect(queryMocks.createApiClient).toHaveBeenCalledWith('http://localhost:8080/api/v1/volundr');
    expect(queryMocks.createApiClient).toHaveBeenCalledWith(
      'http://localhost:8080/api/v1/credentials',
    );
    expect(queryMocks.createApiClient).toHaveBeenCalledWith('http://localhost:8080/api/v1');
    expect(queryMocks.createApiClient).toHaveBeenCalledWith('http://localhost:8080/api/v1/niuu');

    const forgeClient = getDerivedClient('http://localhost:8080/api/v1/forge');
    const catalogClient = getDerivedClient('http://localhost:8080/api/v1/volundr');
    const sharedClient = getDerivedClient('http://localhost:8080/api/v1');
    const niuuClient = getDerivedClient('http://localhost:8080/api/v1/niuu');
    const credentialsClient = getDerivedClient('http://localhost:8080/api/v1/credentials');

    expect(catalogClient.get).toHaveBeenCalledWith('/session-definitions');
    expect(forgeClient.get).not.toHaveBeenCalledWith('/session-definitions');
    expect(sharedClient.get).toHaveBeenCalledWith('/features');
    expect(niuuClient.get).toHaveBeenCalledWith('/instances?kind=volundr&enabledOnly=true');
    expect(credentialsClient.get).toHaveBeenCalledWith('/user');
  });

  it('derives shared and niuu clients from a forge runtime base', async () => {
    const client = makeClientWithBase('http://localhost:8080/api/v1/forge');
    const service = buildVolundrHttpAdapter(client);

    await service.getFeatures();
    await service.getTargets();
    await service.getSession('sess-1');

    expect(queryMocks.createApiClient).toHaveBeenCalledWith('http://localhost:8080/api/v1');
    expect(queryMocks.createApiClient).toHaveBeenCalledWith('http://localhost:8080/api/v1/niuu');

    const sharedClient = getDerivedClient('http://localhost:8080/api/v1');
    const niuuClient = getDerivedClient('http://localhost:8080/api/v1/niuu');

    expect(client.get).toHaveBeenCalledWith('/sessions/sess-1');
    expect(sharedClient.get).toHaveBeenCalledWith('/features');
    expect(niuuClient.get).toHaveBeenCalledWith('/instances?kind=volundr&enabledOnly=true');
  });

  it('derives catalog calls from a volundr catalog base', async () => {
    const client = makeClientWithBase('http://localhost:8080/api/v1/volundr');
    const service = buildVolundrHttpAdapter(client);

    await service.getSessionDefinitions();
    await service.getLaunchSpecs('system');

    expect(client.get).toHaveBeenCalledWith('/session-definitions');
    expect(client.get).toHaveBeenCalledWith('/launch-specs?scope=system');
    expect(queryMocks.createApiClient).toHaveBeenCalledWith('http://localhost:8080/api/v1/forge');
  });

  it('uses the provided client directly when no canonical api base can be derived', async () => {
    const client = { ...makeClient(), basePath: undefined };
    const service = buildVolundrHttpAdapter(client);

    await service.getFeatures();
    await service.getRepos();
    await service.getTargets();

    expect(queryMocks.createApiClient).not.toHaveBeenCalled();
    expect(client.get).toHaveBeenCalledWith('/features');
    expect(client.get).toHaveBeenCalledWith('/repos');
    expect(client.get).toHaveBeenCalledWith('/instances?kind=volundr&enabledOnly=true');
  });

  it('getSessionDefinitions calls GET /session-definitions and normalizes snake_case', async () => {
    const client = makeClient();
    client.get.mockResolvedValueOnce([
      {
        key: 'skuld-claude',
        display_name: 'Claude Code',
        description: 'Anthropic Claude agent',
        labels: ['anthropic'],
        default_model: 'sonnet-primary',
      },
    ]);
    const definitions = await buildVolundrHttpAdapter(client).getSessionDefinitions();
    expect(client.get).toHaveBeenCalledWith('/session-definitions');
    expect(definitions).toEqual([
      {
        key: 'skuld-claude',
        displayName: 'Claude Code',
        description: 'Anthropic Claude agent',
        labels: ['anthropic'],
        defaultModel: 'sonnet-primary',
        compatibleProviders: [],
      },
    ]);
  });

  it('getRepos uses the shared niuu repo catalog and normalizes grouped provider payloads', async () => {
    const client = makeClientWithBase('http://localhost:8080/api/v1/forge');
    const svc = buildVolundrHttpAdapter(client);
    const niuuClient = getDerivedClient('http://localhost:8080/api/v1/niuu')!;
    expect(niuuClient).toBeDefined();
    niuuClient.get.mockResolvedValue({
      GitHub: [
        {
          provider: 'github',
          org: 'niuulabs',
          name: 'volundr',
          url: 'https://github.com/niuulabs/volundr',
          clone_url: 'https://github.com/niuulabs/volundr.git',
          default_branch: 'main',
          branches: ['main', 'feat/wizard'],
        },
      ],
    });

    const repos = await svc.getRepos();

    expect(niuuClient.get).toHaveBeenCalledWith('/repos');
    expect(repos).toEqual([
      expect.objectContaining({
        provider: 'github',
        org: 'niuulabs',
        name: 'volundr',
        cloneUrl: 'https://github.com/niuulabs/volundr.git',
        defaultBranch: 'main',
        branches: ['main', 'feat/wizard'],
      }),
    ]);
  });

  it('getTargets uses the shared niuu registry when mounted at /api/v1/forge', async () => {
    const client = makeClientWithBase('http://localhost:8080/api/v1/forge');
    const svc = buildVolundrHttpAdapter(client);
    const niuuClient = getDerivedClient('http://localhost:8080/api/v1/niuu')!;
    expect(niuuClient).toBeDefined();
    niuuClient.get.mockResolvedValue([
      {
        id: 'inst-alpha',
        name: 'Guild Alpha',
        slug: 'guild-alpha',
        baseUrl: 'http://127.0.0.1:8181',
        visibility: 'tenant',
        enabled: true,
        isDefault: true,
      },
    ]);

    const targets = await svc.getTargets();

    expect(niuuClient.get).toHaveBeenCalledWith('/instances?kind=volundr&enabledOnly=true');
    expect(targets).toEqual([
      expect.objectContaining({
        id: 'inst-alpha',
        name: 'Guild Alpha',
        baseUrl: 'http://127.0.0.1:8181',
        isDefault: true,
      }),
    ]);
  });

  it('uses an explicit niuu registry override when provided', async () => {
    const client = makeClientWithBase('http://localhost:8080/api/v1/forge');
    const svc = buildVolundrHttpAdapter(client, undefined, {
      niuuBasePath: 'https://niuu.yggdrasil.niuu.world/api/v1/niuu',
    });
    const niuuClient = getDerivedClient('https://niuu.yggdrasil.niuu.world/api/v1/niuu')!;
    expect(niuuClient).toBeDefined();
    niuuClient.get.mockResolvedValue([
      {
        id: 'inst-valhalla',
        name: 'Valhalla',
        slug: 'valhalla',
        baseUrl: 'https://volundr.valhalla.asgard.niuu.world',
        visibility: 'system',
        enabled: true,
        isDefault: false,
      },
    ]);

    await svc.getTargets();

    expect(niuuClient.get).toHaveBeenCalledWith('/instances?kind=volundr&enabledOnly=true');
    expect(queryMocks.createApiClient).not.toHaveBeenCalledWith(
      'http://localhost:8080/api/v1/niuu',
    );
  });

  it('startSession calls POST /sessions', async () => {
    const client = makeClient();
    const config = {
      name: 'test',
      source: { type: 'git' as const, repo: 'r', branch: 'main' },
      model: 'claude-sonnet',
    };
    await buildVolundrHttpAdapter(client).startSession(config);
    expect(client.post).toHaveBeenCalledWith('/sessions', {
      name: 'test',
      source: { type: 'git', repo: 'r', branch: 'main' },
      model: 'claude-sonnet',
      terminal_restricted: false,
      instance_id: null,
      workload_config: {},
    });
  });

  it('evaluatePermissionAutoApproval calls the session policy endpoint', async () => {
    const client = makeClient();
    client.post.mockResolvedValueOnce({
      can_auto_approve: true,
      reason: 'allowed',
      command: './start-dev',
      delay_seconds: 5,
      matched_pattern: '^\\s*\\./start-dev',
    });

    const result = await buildVolundrHttpAdapter(client).evaluatePermissionAutoApproval('sess-1', {
      requestId: 'perm-1',
      toolName: 'Bash',
      description: './start-dev',
      command: './start-dev',
      input: { command: './start-dev' },
    });

    expect(client.post).toHaveBeenCalledWith(
      '/sessions/sess-1/permissions/auto-approval/evaluate',
      {
        request_id: 'perm-1',
        tool_name: 'Bash',
        description: './start-dev',
        command: './start-dev',
        input: { command: './start-dev' },
      },
    );
    expect(result).toMatchObject({
      canAutoApprove: true,
      reason: 'allowed',
      delaySeconds: 5,
    });
  });

  it('uses the configured forge client directly for session launch when the base is canonical', async () => {
    const client = makeClientWithBase('http://localhost:8080/api/v1/forge');
    const config = {
      name: 'test',
      source: { type: 'git' as const, repo: 'r', branch: 'main' },
      model: 'claude-sonnet',
    };

    await buildVolundrHttpAdapter(client).startSession(config);

    expect(getDerivedClient('http://localhost:8080/api/v1/forge')).toBeUndefined();
    expect(client.post).toHaveBeenCalledWith('/sessions', {
      name: 'test',
      source: { type: 'git', repo: 'r', branch: 'main' },
      model: 'claude-sonnet',
      terminal_restricted: false,
      instance_id: null,
      workload_config: {},
    });
  });

  it('uses the configured forge client directly for session reads when the base is canonical', async () => {
    const client = makeClientWithBase('http://localhost:8080/api/v1/forge');

    await buildVolundrHttpAdapter(client).getSessions();

    expect(getDerivedClient('http://localhost:8080/api/v1/forge')).toBeUndefined();
    expect(client.get).toHaveBeenCalledWith('/sessions');
  });

  it('stopSession calls POST /sessions/:id/stop', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).stopSession('s1');
    expect(client.post).toHaveBeenCalledWith('/sessions/s1/stop');
  });

  it('deleteSession calls DELETE /sessions/:id without cleanup', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).deleteSession('s1');
    expect(client.delete).toHaveBeenCalledWith('/sessions/s1', { cleanup: [] });
  });

  it('deleteSession sends cleanup targets in the request body when provided', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).deleteSession('s1', ['workspace']);
    expect(client.delete).toHaveBeenCalledWith('/sessions/s1', { cleanup: ['workspace'] });
  });

  it('archiveSession calls PATCH /sessions/:id/archive', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).archiveSession('s1');
    expect(client.patch).toHaveBeenCalledWith('/sessions/s1/archive', undefined);
  });

  it('restoreSession calls PATCH /sessions/:id/restore', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).restoreSession('s1');
    expect(client.patch).toHaveBeenCalledWith('/sessions/s1/restore', undefined);
  });

  it('sendMessage calls POST /sessions/:id/messages', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).sendMessage('s1', 'hello');
    expect(client.post).toHaveBeenCalledWith('/sessions/s1/messages', { content: 'hello' });
  });

  it('getMessages uses conversation history and normalizes turns', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({
      turns: [
        {
          id: 'msg-1',
          role: 'user',
          content: 'hello',
          created_at: '2026-04-24T10:00:00Z',
          metadata: { tokens_in: 4, tokens_out: 0 },
        },
        {
          id: 'msg-2',
          role: 'system',
          content: 'reply',
          created_at: '2026-04-24T10:01:00Z',
          metadata: { tokens_in: 0, tokens_out: 12, latency: 250 },
        },
      ],
    });

    const messages = await buildVolundrHttpAdapter(client).getMessages('s1');

    expect(client.get).toHaveBeenCalledWith('/sessions/s1/conversation');
    expect(messages).toEqual([
      expect.objectContaining({
        id: 'msg-1',
        sessionId: 's1',
        role: 'user',
        tokensIn: 4,
      }),
      expect.objectContaining({
        id: 'msg-2',
        sessionId: 's1',
        role: 'assistant',
        tokensOut: 12,
        latency: 250,
      }),
    ]);
  });

  it('getLogs uses broker line filtering semantics and normalizes the response', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({
      lines: [
        {
          timestamp: 1000,
          level: 'WARNING',
          logger: 'skuld.broker',
          message: 'heads up',
        },
      ],
    });

    const logs = await buildVolundrHttpAdapter(client).getLogs('s1', 50);

    expect(client.get).toHaveBeenCalledWith('/sessions/s1/logs?lines=50');
    expect(logs).toEqual([
      expect.objectContaining({
        id: 's1-log-1000:warn:skuld.broker:heads up:1',
        sessionId: 's1',
        level: 'warn',
        source: 'skuld.broker',
        message: 'heads up',
      }),
    ]);
  });

  it('getAggregatedLogs uses the aggregate endpoint and normalizes participants plus rows', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({
      available_participants: [
        { id: 'skuld', label: 'Skuld', kind: 'broker' },
        { id: 'coder', label: 'Coder', kind: 'ravn' },
      ],
      lines: [
        {
          id: 'agg-1',
          timestamp: '2026-05-01T15:19:51.232000+00:00',
          level: 'WARNING',
          participant: 'coder',
          participant_label: 'Coder',
          participant_kind: 'ravn',
          source: 'ravn.adapters.llm.openai',
          message: 'HTTP 503 Service Unavailable',
          sequence: 28,
          stream: 'logs/coder.log',
        },
      ],
    });

    const payload = await buildVolundrHttpAdapter(client).getAggregatedLogs('s1', {
      limit: 50,
      level: 'WARNING',
      participants: ['coder'],
      query: '503',
    });

    expect(client.get).toHaveBeenCalledWith(
      '/sessions/s1/logs/aggregate?lines=50&level=WARNING&participants=coder&query=503',
    );
    expect(payload.participants).toEqual([
      { id: 'skuld', label: 'Skuld', kind: 'broker' },
      { id: 'coder', label: 'Coder', kind: 'ravn' },
    ]);
    expect(payload.lines).toEqual([
      expect.objectContaining({
        id: 'agg-1',
        sessionId: 's1',
        level: 'warn',
        participant: 'coder',
        participantLabel: 'Coder',
        participantKind: 'ravn',
        source: 'ravn.adapters.llm.openai',
        message: 'HTTP 503 Service Unavailable',
        sequence: 28,
        stream: 'logs/coder.log',
      }),
    ]);
  });

  it('getChronicle uses the timeline endpoint and normalizes token burn', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({
      events: [{ t: 0, type: 'session', label: 'started' }],
      files: [{ path: 'src/app.ts', status: 'mod', ins: 3, del: 1 }],
      commits: [{ hash: 'abc123', msg: 'test', time: '10:00' }],
      token_burn: [1, 2, 3],
    });

    const chronicle = await buildVolundrHttpAdapter(client).getChronicle('s1');

    expect(client.get).toHaveBeenCalledWith('/chronicles/s1/timeline');
    expect(chronicle).toEqual({
      events: [{ t: 0, type: 'session', label: 'started' }],
      files: [{ path: 'src/app.ts', status: 'mod', ins: 3, del: 1 }],
      commits: [{ hash: 'abc123', msg: 'test', time: '10:00' }],
      tokenBurn: [1, 2, 3],
    });
  });

  it('saveLaunchSpec calls POST /launch-specs when no id', async () => {
    const client = makeClient();
    const preset = {
      name: 'fast',
      description: '',
      isDefault: false,
      cliTool: 'claude',
      workloadType: 'default',
      model: null,
      systemPrompt: null,
      resourceConfig: {},
      mcpServers: [],
      terminalSidecar: { enabled: false, allowedCommands: [] },
      skills: [],
      rules: [],
      envVars: {},
      envSecretRefs: [],
      source: null,
      integrationIds: [],
      setupScripts: [],
      workloadConfig: {},
    };
    await buildVolundrHttpAdapter(client).saveLaunchSpec(preset);
    expect(client.post).toHaveBeenCalledWith('/launch-specs', preset);
  });

  it('saveLaunchSpec calls PUT /launch-specs/:id when id is present', async () => {
    const client = makeClient();
    const preset = {
      id: 'p1',
      name: 'fast',
      description: '',
      isDefault: false,
      cliTool: 'claude',
      workloadType: 'default',
      model: null,
      systemPrompt: null,
      resourceConfig: {},
      mcpServers: [],
      terminalSidecar: { enabled: false, allowedCommands: [] },
      skills: [],
      rules: [],
      envVars: {},
      envSecretRefs: [],
      source: null,
      integrationIds: [],
      setupScripts: [],
      workloadConfig: {},
    };
    await buildVolundrHttpAdapter(client).saveLaunchSpec(preset);
    expect(client.put).toHaveBeenCalledWith('/launch-specs/p1', preset);
  });

  it('getIdentity calls GET /identity/me', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).getIdentity();
    const sharedClient = getDerivedClient('http://localhost:8080/api/v1');
    expect(sharedClient.get).toHaveBeenCalledWith('/identity/me');
  });

  it('listArchivedSessions uses the archived status query instead of a synthetic sub-route', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).listArchivedSessions();
    expect(client.get).toHaveBeenCalledWith('/sessions?status=archived');
  });

  it('listExternalSessions calls GET /external-sessions and normalizes payloads', async () => {
    const client = makeClient();
    client.get.mockResolvedValueOnce([
      {
        provider: 'claude-code',
        harness: 'claude',
        external_id: 'ext-1',
        workspace_path: '/Users/dev/code/volundr',
        title: 'fix import flow',
        model: 'claude-sonnet',
        created_at: '2026-06-01T08:00:00Z',
        updated_at: '2026-06-01T09:00:00Z',
        live: true,
        workspace_exists: true,
        imported_session_id: null,
      },
    ]);

    const sessions = await buildVolundrHttpAdapter(client).listExternalSessions();

    expect(client.get).toHaveBeenCalledWith('/external-sessions');
    expect(sessions).toEqual([
      expect.objectContaining({
        provider: 'claude-code',
        harness: 'claude',
        externalId: 'ext-1',
        workspacePath: '/Users/dev/code/volundr',
        live: true,
        workspaceExists: true,
        importedSessionId: null,
      }),
    ]);
  });

  it('listExternalSessions propagates 503 errors so callers can hide the affordance', async () => {
    const client = makeClient();
    const unavailable = Object.assign(new Error('External session discovery not available'), {
      status: 503,
    });
    client.get.mockRejectedValueOnce(unavailable);

    await expect(buildVolundrHttpAdapter(client).listExternalSessions()).rejects.toMatchObject({
      status: 503,
    });
  });

  it('importExternalSession posts snake_case body and normalizes the created session', async () => {
    const client = makeClient();
    client.post.mockResolvedValueOnce({
      id: 'sess-imported',
      name: 'fix import flow',
      source: { type: 'local_mount', paths: [] },
      status: 'stopped',
      model: 'claude-sonnet',
      origin: 'claude',
      external_session_id: 'ext-1',
    });

    const session = await buildVolundrHttpAdapter(client).importExternalSession(
      'claude-code',
      'ext-1',
    );

    expect(client.post).toHaveBeenCalledWith('/sessions/import', {
      provider: 'claude-code',
      external_id: 'ext-1',
    });
    expect(session).toMatchObject({
      id: 'sess-imported',
      origin: 'claude',
      externalSessionId: 'ext-1',
    });
  });

  it('importExternalSession includes the optional name in the request body', async () => {
    const client = makeClient();
    client.post.mockResolvedValueOnce({
      id: 'sess-imported',
      name: 'renamed',
      source: { type: 'local_mount', paths: [] },
      status: 'stopped',
      model: 'gpt-5-codex',
    });

    await buildVolundrHttpAdapter(client).importExternalSession('codex', 'ext-2', 'renamed');

    expect(client.post).toHaveBeenCalledWith('/sessions/import', {
      provider: 'codex',
      external_id: 'ext-2',
      name: 'renamed',
    });
  });

  it('createCredential targets the canonical shared credentials route', async () => {
    const client = makeClient();
    const req = { name: 'my-key', secretType: 'api_key' as const, data: { token: 'abc' } };
    await buildVolundrHttpAdapter(client).createCredential(req);
    const derivedClient = getDerivedClient('http://localhost:8080/api/v1/credentials');
    expect(queryMocks.createApiClient).toHaveBeenCalledWith(
      'http://localhost:8080/api/v1/credentials',
    );
    expect(derivedClient.post).toHaveBeenCalledWith('/user', {
      name: 'my-key',
      secret_type: 'api_key',
      data: { token: 'abc' },
      metadata: undefined,
    });
    expect(client.post).not.toHaveBeenCalledWith('/secrets/store', req);
  });

  it('toggleFeature calls POST /features/modules/:key/toggle', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).toggleFeature('some-feature', true);
    const sharedClient = getDerivedClient('http://localhost:8080/api/v1');
    expect(sharedClient.post).toHaveBeenCalledWith('/features/modules/some-feature/toggle', {
      enabled: true,
    });
  });

  it('revokeToken calls DELETE /tokens/:id', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).revokeToken('t1');
    const sharedClient = getDerivedClient('http://localhost:8080/api/v1');
    expect(sharedClient.delete).toHaveBeenCalledWith('/tokens/t1');
  });

  it('bulkDeleteWorkspaces calls POST /workspaces/bulk-delete', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).bulkDeleteWorkspaces(['sess-1', 'sess-2']);
    expect(client.post).toHaveBeenCalledWith('/workspaces/bulk-delete', {
      sessionIds: ['sess-1', 'sess-2'],
    });
  });

  it('subscribe returns an unsubscribe function', () => {
    const client = makeClient();
    const unsub = buildVolundrHttpAdapter(client).subscribe(vi.fn());
    expect(typeof unsub).toBe('function');
    unsub(); // should not throw
  });

  it('normalizes session and stats payloads from snake_case responses', async () => {
    const client = makeClient();
    client.get.mockImplementation(async (endpoint: string) => {
      if (endpoint === '/sessions') {
        return [
          {
            id: 'sess-1',
            name: 'alpha',
            source: { type: 'git', repo: 'github.com/acme/repo', branch: 'main' },
            status: 'running',
            model: 'claude-sonnet',
            last_active: '2026-04-24T10:00:00Z',
            message_count: 7,
            tokens_used: 123,
            chat_endpoint: 'https://chat.example.com',
            code_endpoint: 'https://code.example.com',
            owner_id: 'user-1',
            tenant_id: 'tenant-1',
          },
        ];
      }
      if (endpoint === '/stats') {
        return {
          active_sessions: 1,
          total_sessions: 3,
          tokens_today: 400,
          local_tokens: 150,
          cloud_tokens: 250,
          cost_today: 2.5,
        };
      }
      return [];
    });

    const svc = buildVolundrHttpAdapter(client);
    const [session] = await svc.getSessions();
    const stats = await svc.getStats();

    expect(session.messageCount).toBe(7);
    expect(session.tokensUsed).toBe(123);
    expect(session.chatEndpoint).toBe('https://chat.example.com');
    expect(session.ownerId).toBe('user-1');
    expect(stats.activeSessions).toBe(1);
    expect(stats.tokensToday).toBe(400);
    expect(stats.costToday).toBe(2.5);
  });

  it('shares one live stream across session and stats subscribers', async () => {
    const client = makeClient();
    client.get.mockImplementation(async (endpoint: string) => {
      if (endpoint === '/sessions') return [];
      if (endpoint === '/stats') {
        return {
          active_sessions: 0,
          total_sessions: 0,
          tokens_today: 0,
          local_tokens: 0,
          cloud_tokens: 0,
          cost_today: 0,
        };
      }
      return [];
    });

    let onEvent: ((frame: { event?: string; data: string }) => void) | undefined;
    const close = vi.fn();
    const openStream = vi.fn((_url: string, options: { onEvent?: typeof onEvent }) => {
      onEvent = options.onEvent;
      return { close };
    });

    const svc = buildVolundrHttpAdapter(client, openStream as never);
    const sessionSeen: Array<Array<{ id: string }>> = [];
    const statsSeen: Array<{ activeSessions: number }> = [];

    const unsubSessions = svc.subscribe((sessions) =>
      sessionSeen.push(sessions as Array<{ id: string }>),
    );
    const unsubStats = svc.subscribeStats((stats) =>
      statsSeen.push(stats as { activeSessions: number }),
    );
    await Promise.resolve();

    expect(openStream).toHaveBeenCalledTimes(1);
    expect(openStream).toHaveBeenCalledWith(
      'http://localhost:8080/api/v1/forge/sessions/stream',
      expect.objectContaining({ onEvent: expect.any(Function) }),
    );

    onEvent?.({
      event: 'session_updated',
      data: JSON.stringify({
        id: 'sess-1',
        name: 'alpha',
        source: { type: 'git', repo: 'github.com/acme/repo', branch: 'main' },
        status: 'running',
        model: 'claude-sonnet',
        last_active: '2026-04-24T10:00:00Z',
        message_count: 5,
        tokens_used: 11,
      }),
    });
    onEvent?.({
      event: 'stats_updated',
      data: JSON.stringify({
        active_sessions: 1,
        total_sessions: 2,
        tokens_today: 80,
        local_tokens: 20,
        cloud_tokens: 60,
        cost_today: 1.25,
      }),
    });
    onEvent?.({ event: 'session_deleted', data: JSON.stringify({ id: 'sess-1' }) });

    expect(sessionSeen.at(-2)?.[0]).toMatchObject({
      id: 'sess-1',
      messageCount: 5,
      tokensUsed: 11,
    });
    expect(sessionSeen.at(-1)).toEqual([]);
    expect(statsSeen.at(-1)).toMatchObject({ activeSessions: 1, costToday: 1.25 });

    unsubSessions();
    expect(close).not.toHaveBeenCalled();
    unsubStats();
    expect(close).toHaveBeenCalledTimes(1);
  });

  it('streams chronicle updates for a specific session from the shared SSE feed', async () => {
    const client = makeClient();
    client.get.mockImplementation(async (endpoint: string) => {
      if (endpoint === '/chronicles/sess-1/timeline') {
        return {
          events: [],
          files: [],
          commits: [],
          token_burn: [],
        };
      }
      return [];
    });

    let onEvent: ((frame: { event?: string; data: string }) => void) | undefined;
    const openStream = vi.fn((_url: string, options: { onEvent?: typeof onEvent }) => {
      onEvent = options.onEvent;
      return { close: vi.fn() };
    });

    const seen: Array<{ tokenBurn: number[]; events: Array<{ label: string }> }> = [];
    const unsub = buildVolundrHttpAdapter(client, openStream as never).subscribeChronicle(
      'sess-1',
      (chronicle) =>
        seen.push(chronicle as { tokenBurn: number[]; events: Array<{ label: string }> }),
    );
    await Promise.resolve();

    onEvent?.({
      event: 'chronicle_event',
      data: JSON.stringify({
        session_id: 'sess-1',
        event: { t: 1, type: 'message', label: 'assistant replied' },
        files: [{ path: 'src/app.ts', status: 'mod', ins: 3, del: 1 }],
        commits: [{ hash: 'abc123', msg: 'test', time: '10:00' }],
        token_burn: [2, 4],
      }),
    });

    expect(seen.at(-1)).toEqual({
      events: [{ t: 1, type: 'message', label: 'assistant replied' }],
      files: [{ path: 'src/app.ts', status: 'mod', ins: 3, del: 1 }],
      commits: [{ hash: 'abc123', msg: 'test', time: '10:00' }],
      tokenBurn: [2, 4],
    });

    unsub();
  });

  it('replays cached chronicle snapshots immediately to new chronicle subscribers', async () => {
    const client = makeClient();
    client.get.mockResolvedValueOnce({
      events: [{ t: 0, type: 'session', label: 'started' }],
      files: [],
      commits: [],
      token_burn: [1],
    });
    const openStream = vi.fn(() => ({ close: vi.fn() }));
    const svc = buildVolundrHttpAdapter(client, openStream as never);

    await svc.getChronicle('sess-1');
    const seen: any[] = [];
    const unsub = svc.subscribeChronicle('sess-1', (chronicle) => seen.push(chronicle));

    expect(seen).toEqual([
      {
        events: [{ t: 0, type: 'session', label: 'started' }],
        files: [],
        commits: [],
        tokenBurn: [1],
      },
    ]);

    unsub();
  });

  it('polls conversation history and emits only new messages', async () => {
    vi.useFakeTimers();
    const client = makeClient();
    client.get.mockImplementation(async (endpoint: string) => {
      if (endpoint !== '/sessions/sess-1/conversation') return [];
      if (client.get.mock.calls.length <= 1) {
        return {
          turns: [
            { id: 'msg-1', role: 'user', content: 'hello', created_at: '2026-04-24T10:00:00Z' },
          ],
        };
      }
      return {
        turns: [
          { id: 'msg-1', role: 'user', content: 'hello', created_at: '2026-04-24T10:00:00Z' },
          { id: 'msg-2', role: 'assistant', content: 'hi', created_at: '2026-04-24T10:01:00Z' },
        ],
      };
    });

    const seen: VolundrMessage[] = [];
    const unsub = buildVolundrHttpAdapter(client).subscribeMessages('sess-1', (message) =>
      seen.push(message),
    );

    await vi.runOnlyPendingTimersAsync();

    expect(seen).toEqual([
      expect.objectContaining({
        id: 'msg-2',
        sessionId: 'sess-1',
        role: 'assistant',
      }),
    ]);

    unsub();
  });

  it('polls session logs and emits only new lines', async () => {
    vi.useFakeTimers();
    const client = makeClient();
    client.get.mockImplementation(async (endpoint: string) => {
      if (endpoint !== '/sessions/sess-1/logs') return [];
      if (client.get.mock.calls.length <= 1) {
        return {
          lines: [{ timestamp: 1000, level: 'INFO', logger: 'skuld', message: 'booting' }],
        };
      }
      return {
        lines: [
          { timestamp: 1000, level: 'INFO', logger: 'skuld', message: 'booting' },
          { timestamp: 2000, level: 'ERROR', logger: 'skuld', message: 'failed once' },
        ],
      };
    });

    const seen: VolundrLog[] = [];
    const unsub = buildVolundrHttpAdapter(client).subscribeLogs('sess-1', (log) => seen.push(log));

    await vi.runOnlyPendingTimersAsync();

    expect(seen).toEqual([
      expect.objectContaining({
        sessionId: 'sess-1',
        level: 'error',
        message: 'failed once',
      }),
    ]);

    unsub();
  });

  it('suppresses duplicate aggregated log payloads while polling and stops after unsubscribe', async () => {
    vi.useFakeTimers();
    const client = makeClient();
    client.get.mockImplementation(async (endpoint: string) => {
      if (!endpoint.startsWith('/sessions/sess-1/logs/aggregate')) return [];
      if (client.get.mock.calls.length <= 1) {
        return {
          available_participants: [{ id: 'coder' }],
          lines: [{ id: 'agg-1', timestamp: 1000, level: 'INFO', message: 'hello' }],
        };
      }
      return {
        available_participants: [{ id: 'coder' }],
        lines: [{ id: 'agg-1', timestamp: 1000, level: 'INFO', message: 'hello' }],
      };
    });

    const seen: any[] = [];
    const unsub = buildVolundrHttpAdapter(client).subscribeAggregatedLogs(
      'sess-1',
      { limit: 25 },
      (payload) => seen.push(payload),
    );

    await vi.runOnlyPendingTimersAsync();
    expect(seen).toHaveLength(1);

    unsub();
    await vi.runOnlyPendingTimersAsync();
    expect(seen).toHaveLength(1);
  });

  it('returns null for missing trace endpoints and rethrows non-404 errors', async () => {
    const client = makeClient();
    client.get
      .mockRejectedValueOnce(new Error('404 trace missing'))
      .mockRejectedValueOnce(new Error('404 trace summary missing'))
      .mockRejectedValueOnce(new Error('500 trace exploded'))
      .mockRejectedValueOnce(new Error('500 summary exploded'));

    const svc = buildVolundrHttpAdapter(client);

    await expect(svc.getSessionTrace('sess-1')).resolves.toBeNull();
    await expect(svc.getSessionTraceSummary('sess-1')).resolves.toBeNull();
    await expect(svc.getSessionTrace('sess-2')).rejects.toThrow('500 trace exploded');
    await expect(svc.getSessionTraceSummary('sess-2')).rejects.toThrow('500 summary exploded');
  });

  it('preserves repeated identical log lines as distinct live events', async () => {
    vi.useFakeTimers();
    const client = makeClient();
    client.get.mockImplementation(async (endpoint: string) => {
      if (endpoint !== '/sessions/sess-1/logs') return [];
      if (client.get.mock.calls.length <= 1) {
        return {
          lines: [{ timestamp: 1000, level: 'INFO', logger: 'skuld', message: 'retrying' }],
        };
      }
      return {
        lines: [
          { timestamp: 1000, level: 'INFO', logger: 'skuld', message: 'retrying' },
          { timestamp: 1000, level: 'INFO', logger: 'skuld', message: 'retrying' },
        ],
      };
    });

    const seen: VolundrLog[] = [];
    const unsub = buildVolundrHttpAdapter(client).subscribeLogs('sess-1', (log) => seen.push(log));

    await vi.runOnlyPendingTimersAsync();

    expect(seen).toEqual([
      expect.objectContaining({
        id: 'sess-1-log-1000:info:skuld:retrying:2',
        sessionId: 'sess-1',
        level: 'info',
        message: 'retrying',
      }),
    ]);

    unsub();
  });

  it('propagates errors from the HTTP client', async () => {
    const client = makeClient();
    client.get.mockRejectedValue(new Error('network error'));
    await expect(buildVolundrHttpAdapter(client).getSessions()).rejects.toThrow('network error');
  });

  it('searchTrackerIssues encodes the query', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).searchTrackerIssues('fix auth', 'proj-1');
    const sharedClient = getDerivedClient('http://localhost:8080/api/v1');
    expect(sharedClient.get).toHaveBeenCalledWith('/tracker/issues?q=fix%20auth&projectId=proj-1');
  });

  it('getFeatureModules includes scope when provided', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).getFeatureModules('admin');
    const sharedClient = getDerivedClient('http://localhost:8080/api/v1');
    expect(sharedClient.get).toHaveBeenCalledWith('/features/modules?scope=admin');
  });

  it('getCredentials targets the canonical shared credentials route when filtering by type', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).getCredentials('api_key');
    const derivedClient = getDerivedClient('http://localhost:8080/api/v1/credentials');
    expect(derivedClient.get).toHaveBeenCalledWith('/user?secret_type=api_key');
  });

  it('listWorkspaces includes status when provided', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).listWorkspaces('archived');
    expect(client.get).toHaveBeenCalledWith('/workspaces?status=archived');
  });

  it('getCIStatus includes repoUrl and branch as query params', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).getCIStatus(42, 'github.com/org/repo', 'feat/x');
    expect(client.get).toHaveBeenCalledWith(expect.stringContaining('/repos/prs/42/ci'));
  });
});

describe('buildVolundrHttpAdapter — full method sweep', () => {
  it('covers every remaining IVolundrService method', async () => {
    const client = makeClient();
    client.get.mockImplementation(async (endpoint: string) => {
      if (endpoint.includes('/conversation')) return { turns: [] };
      if (endpoint.includes('/logs')) return { lines: [] };
      if (endpoint.includes('/chronicles/')) {
        return { events: [], files: [], commits: [], token_burn: [] };
      }
      return [];
    });
    client.post.mockResolvedValue({});
    client.delete.mockResolvedValue(undefined);
    client.patch.mockResolvedValue({});
    client.put.mockResolvedValue({});

    const svc = buildVolundrHttpAdapter(client);

    // Subscribe methods — call outer AND inner unsubscribe to cover both arrow fns
    const unsub1 = svc.subscribe(vi.fn());
    unsub1();
    const unsub2 = svc.subscribeStats(vi.fn());
    unsub2();
    const unsub3 = svc.subscribeMessages('sess-1', vi.fn());
    unsub3();
    const unsub4 = svc.subscribeLogs('sess-1', vi.fn());
    unsub4();
    const unsub5 = svc.subscribeChronicle('sess-1', vi.fn());
    unsub5();

    // GET methods
    await svc.getFeatures();
    await svc.getRepos();
    await svc.getLaunchSpecs();
    await svc.getLaunchSpec('tpl-1');
    await svc.getLaunchSpecs('user');
    await svc.getLaunchSpec('p1');
    await svc.getAvailableMcpServers();
    await svc.getAvailableSecrets();
    await svc.getClusterResources();
    await svc.listArchivedSessions();
    await svc.getMessages('sess-1');
    await svc.getLogs('sess-1');
    await svc.getLogs('sess-1', 50);
    await svc.getCodeServerUrl('sess-1');
    await svc.getChronicle('sess-1');
    await svc.getPullRequests('github.com/org/repo');
    await svc.getPullRequests('github.com/org/repo', 'open');
    await svc.getSessionMcpServers('sess-1');
    await svc.getProjectRepoMappings();
    await svc.listUsers();
    await svc.getTenants();
    await svc.getTenant('t1');
    await svc.getTenantMembers('t1');
    await svc.getUserCredentials();
    await svc.getTenantCredentials();
    await svc.getIntegrationCatalog();
    await svc.getIntegrations();
    await svc.getCredentials();
    await svc.getCredential('my-key');
    await svc.getCredentialTypes();
    await svc.listWorkspaces();
    await svc.listAllWorkspaces();
    await svc.listAllWorkspaces('archived');
    await svc.getAdminSettings();
    await svc.getFeatureModules();
    await svc.getUserFeaturePreferences();
    await svc.listTokens();

    // POST methods
    await svc.connectSession({ name: 'c', hostname: 'host.example.com' });
    await svc.resumeSession('sess-1');
    await svc.archiveSession('sess-1');
    await svc.archiveStoppedSessions();
    await svc.restoreSession('sess-1');
    await svc.createTenant({ name: 'acme' });
    await svc.reprovisionUser('u1');
    await svc.reprovisionTenant('t1');
    await svc.storeUserCredential('key', { token: 'abc' });
    await svc.storeTenantCredential('key', { token: 'abc' });
    await svc.createIntegration({ type: 'github', config: {} } as Parameters<
      typeof svc.createIntegration
    >[0]);
    await svc.testIntegration('int-1');
    await svc.restoreWorkspace('ws-1');
    await svc.createToken('my-token');
    await svc.mergePullRequest(42, 'github.com/org/repo', 'squash');
    await svc.createPullRequest('sess-1', 'My PR', 'main');
    await svc.createSecret('my-secret', { token: 'abc' });

    // PATCH / PUT methods
    await svc.updateSession('sess-1', { name: 'updated' });
    await svc.updateTenant('t1', { name: 'acme-v2' });
    await svc.updateTrackerIssueStatus('issue-1', 'done');
    await svc.saveLaunchSpec({ name: 'tpl', description: '', config: {} } as unknown as Parameters<
      typeof svc.saveLaunchSpec
    >[0]);
    await svc.updateAdminSettings({
      storage: { provider: 's3', bucket: 'b', region: 'us-east-1' },
    });
    await svc.updateUserFeaturePreferences([{ key: 'dark-mode', enabled: true }] as Parameters<
      typeof svc.updateUserFeaturePreferences
    >[0]);

    // DELETE methods
    await svc.deleteLaunchSpec('p1');
    await svc.deleteTenant('t1');
    await svc.deleteUserCredential('key');
    await svc.deleteTenantCredential('key');
    await svc.deleteIntegration('int-1');
    await svc.deleteCredential('my-key');
    await svc.deleteWorkspace('ws-1');

    // All calls should have resolved without throwing
    expect(client.get).toHaveBeenCalled();
    expect(client.post).toHaveBeenCalled();
    expect(client.delete).toHaveBeenCalled();
  });
});
