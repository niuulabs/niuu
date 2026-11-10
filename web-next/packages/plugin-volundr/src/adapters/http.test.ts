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
import { SessionForkError, SessionReadStateConflictError } from '../domain/session';
import { ApiClientError } from '@niuulabs/query';

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

it('renames through the Forge PUT contract and normalizes the returned session', async () => {
  const client = makeClient();
  client.put.mockResolvedValue({ id: 'sess-1', name: 'review-renamed', status: 'running' });
  const updated = await buildVolundrHttpAdapter(client).updateSession('sess-1', {
    name: 'review-renamed',
  });
  expect(client.put).toHaveBeenCalledExactlyOnceWith('/sessions/sess-1', {
    name: 'review-renamed',
  });
  expect(client.patch).not.toHaveBeenCalled();
  expect(updated).toMatchObject({ id: 'sess-1', name: 'review-renamed', status: 'running' });
});

it('forks a session through its chronicle on its own host, then names and starts the child', async () => {
  const client = makeClient();
  const service = buildVolundrHttpAdapter(client);
  client.get.mockImplementation(async (endpoint: string) =>
    endpoint.startsWith('/sessions/parent/chronicle')
      ? { id: 'chron-1' }
      : { id: 'parent', name: 'demo', status: 'running', instance_id: 'thor' },
  );
  await service.getSession('parent', { instanceId: 'thor' });
  client.post.mockImplementation(async (endpoint: string) =>
    endpoint.startsWith('/chronicles/')
      ? { id: 'child', name: 'demo (reforged)', status: 'created' }
      : undefined,
  );
  client.put.mockResolvedValue({ id: 'child', name: 'demo-side', status: 'created' });

  const child = await service.forkSession('parent', 'demo-side');

  expect(client.get).toHaveBeenCalledWith('/sessions/parent/chronicle?instance_id=thor');
  expect(client.post).toHaveBeenNthCalledWith(1, '/chronicles/chron-1/reforge?instance_id=thor');
  expect(client.put).toHaveBeenCalledWith('/sessions/child?instance_id=thor', {
    name: 'demo-side',
  });
  expect(client.post).toHaveBeenNthCalledWith(2, '/sessions/child/start?instance_id=thor');
  expect(child).toMatchObject({ id: 'child', name: 'demo-side', instanceId: 'thor' });
});

it('names the child it created when naming or starting it fails', async () => {
  const client = makeClient();
  const service = buildVolundrHttpAdapter(client);
  client.get.mockResolvedValue({ id: 'chron-1' });
  client.post.mockImplementation(async (endpoint: string) => {
    if (endpoint.startsWith('/chronicles/'))
      return { id: 'child', name: 'demo', status: 'created' };
    throw new ApiClientError('API request failed: 409', 409, 'No free session slot');
  });
  client.put.mockResolvedValue({ id: 'child', name: 'demo-side', status: 'created' });

  const failure = await service.forkSession('parent', 'demo-side').catch((error) => error);
  expect(failure).toBeInstanceOf(SessionForkError);
  expect(failure).toMatchObject({ childId: 'child' });
  expect(failure.message).toContain('could not start it: No free session slot');

  client.put.mockRejectedValue(new Error('name taken'));
  const unnamed = await service.forkSession('parent', 'demo-side').catch((error) => error);
  expect(unnamed.message).toContain('could not name it: name taken');
  client.put.mockRejectedValue('odd');
  const odd = await service.forkSession('parent', 'demo-side').catch((error) => error);
  expect(odd.message).toContain('could not name it: odd');
});

it('refuses a fork outright when the session has no chronicle', async () => {
  const client = makeClient();
  client.get.mockRejectedValue(new ApiClientError('API request failed: 404', 404, 'No chronicle'));
  await expect(buildVolundrHttpAdapter(client).forkSession('parent', 'x-side')).rejects.toThrow(
    'API request failed: 404',
  );
  expect(client.post).not.toHaveBeenCalled();
});

it('assigns a project from a different host while keeping the session owner selector', async () => {
  const client = makeClient();
  const service = buildVolundrHttpAdapter(client);
  client.get.mockResolvedValue({ session_id: 'worker', revision: 0, coordination: null });
  expect(await service.getSessionProject('worker', { instanceId: 'thor' })).toMatchObject({
    projectId: null,
    revision: 0,
  });
  expect(client.get).toHaveBeenLastCalledWith('/sessions/worker/project?instance_id=thor', {
    signal: undefined,
  });
  client.put.mockResolvedValue({
    session_id: 'worker',
    revision: 1,
    coordination: { project_id: 'kit', role: 'worker' },
  });
  expect(
    await service.assignSessionProject(
      'worker',
      { projectId: 'kit', projectInstanceId: 'build', expectedRevision: 0 },
      { instanceId: 'thor' },
    ),
  ).toMatchObject({ projectId: 'kit', revision: 1 });
  expect(client.put).toHaveBeenLastCalledWith('/sessions/worker/project?instance_id=thor', {
    project_id: 'kit',
    project_instance_id: 'build',
    expected_revision: 0,
  });
  client.get.mockResolvedValue([]);
  const signal = new AbortController().signal;
  await service.getProjects({ instanceId: 'build', signal });
  expect(client.get).toHaveBeenLastCalledWith('/projects?instance_id=build', { signal });
  client.get.mockResolvedValue({});
  await expect(service.getSessionProject('worker')).rejects.toThrow('versioned project assignment');
  client.put.mockRejectedValue(new Error('Project host unavailable'));
  await expect(
    service.assignSessionProject('worker', {
      projectId: 'kit',
      projectInstanceId: 'build',
      expectedRevision: 0,
    }),
  ).rejects.toThrow('Project host unavailable');
});

describe('getSessionChanges', () => {
  const tagged = (id: string, instance = 'build') => ({
    id,
    name: id,
    status: 'running',
    instance_id: instance,
  });

  it('reads one host since a cursor and keeps its routing to exactly the listed sessions', async () => {
    const client = makeClient();
    const service = buildVolundrHttpAdapter(client);
    client.get.mockResolvedValueOnce({
      cursor: '10',
      ids: ['kept', 'gone'],
      sessions: [tagged('kept'), tagged('gone')],
    });
    await service.getSessionChanges!('0', { instanceId: 'build', archived: false });
    client.get.mockResolvedValueOnce({ cursor: '12', ids: ['kept'], sessions: [] });

    const changes = await service.getSessionChanges!('10', {
      instanceId: 'build',
      archived: false,
    });

    expect(client.get).toHaveBeenLastCalledWith('/sessions?changed_since=10&instance_id=build');
    expect(changes).toEqual({ cursor: '12', ids: ['kept'], sessions: [] });
    await service.stopSession('kept');
    await service.stopSession('gone');
    expect(client.post).toHaveBeenCalledWith('/sessions/kept/stop?instance_id=build');
    expect(client.post).toHaveBeenCalledWith('/sessions/gone/stop');
  });

  it('reads the archive with its signal and routes a restore to the listing host', async () => {
    const client = makeClient();
    const service = buildVolundrHttpAdapter(client);
    const signal = new AbortController().signal;
    client.get.mockResolvedValueOnce({
      cursor: '3',
      ids: ['old'],
      sessions: [{ ...tagged('old'), status: 'archived' }],
    });

    const changes = await service.getSessionChanges!('0', {
      instanceId: 'build',
      archived: true,
      signal,
    });

    expect(client.get).toHaveBeenLastCalledWith(
      '/sessions?changed_since=0&instance_id=build&status=archived',
      { signal },
    );
    expect(changes.sessions.map((session) => session.id)).toEqual(['old']);
    await service.restoreSession('old');
    expect(client.patch).toHaveBeenCalledWith('/sessions/old/restore?instance_id=build', undefined);
    client.get.mockResolvedValueOnce({ cursor: '4', ids: [], sessions: [] });
    await service.getSessionChanges!('3', { instanceId: 'build', archived: true });
    await service.restoreSession('old');
    expect(client.patch).toHaveBeenLastCalledWith('/sessions/old/restore', undefined);
  });

  it('takes a whole inventory from an older gateway as a full answer', async () => {
    const client = makeClient();
    client.get.mockResolvedValueOnce([tagged('a'), tagged('b')]);
    const changes = await buildVolundrHttpAdapter(client).getSessionChanges!('9', {
      instanceId: 'build',
      archived: false,
    });
    expect(changes.cursor).toBe('0');
    expect(changes.ids).toEqual(['a', 'b']);
    expect(changes.sessions.map((session) => session.id)).toEqual(['a', 'b']);
  });

  it.each([{}, { cursor: 1, ids: [], sessions: [] }, { cursor: '1', ids: [] }, null])(
    'refuses a malformed answer %j',
    async (payload) => {
      const client = makeClient();
      client.get.mockResolvedValueOnce(payload);
      await expect(
        buildVolundrHttpAdapter(client).getSessionChanges!('0', {
          instanceId: 'build',
          archived: false,
        }),
      ).rejects.toThrow('Invalid session changes from build');
    },
  );
});

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
        personaName: 'reviewer',
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
      persona_name: 'reviewer',
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
    expect(session.startup).toBeNull();

    expect(
      normalizeSession({
        id: 'sess-2',
        name: 'booting',
        source: { type: 'git', repo: 'r', branch: 'main' },
        status: 'provisioning',
        model: 'sonnet',
        startup: {
          started_at: '2026-11-06T12:00:00Z',
          progress_error: 'events forbidden',
          stages: [
            {
              key: 'schedule',
              label: 'Scheduling',
              state: 'done',
              detail: 'On node valhalla-w-07',
              started_at: '2026-11-06T12:00:01Z',
              finished_at: '2026-11-06T12:00:02Z',
            },
            { key: 'containers', label: 'Starting containers', state: 'pending' },
          ],
        },
      } as any).startup,
    ).toEqual({
      startedAt: '2026-11-06T12:00:00Z',
      progressError: 'events forbidden',
      stages: [
        {
          key: 'schedule',
          label: 'Scheduling',
          state: 'done',
          detail: 'On node valhalla-w-07',
          startedAt: '2026-11-06T12:00:01Z',
          finishedAt: '2026-11-06T12:00:02Z',
        },
        {
          key: 'containers',
          label: 'Starting containers',
          state: 'pending',
          detail: null,
          startedAt: null,
          finishedAt: null,
        },
      ],
    });
    expect(
      normalizeSession({
        id: 'sess-3',
        name: 'old',
        source: { type: 'git', repo: 'r', branch: 'main' },
        status: 'running',
        model: 'sonnet',
        startup: { started_at: '2026-11-06T12:00:00Z' },
      } as any).startup,
    ).toEqual({ startedAt: '2026-11-06T12:00:00Z', stages: [], progressError: null });

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

  it('derives needsAttention from awaiting_input or the explicit flag', () => {
    const base = {
      id: 's',
      name: 'a',
      source: { type: 'git' as const, repo: 'r', branch: 'main' },
      status: 'running' as const,
      model: 'm',
    };
    expect(normalizeSession({ ...base, activity_state: 'awaiting_input' }).needsAttention).toBe(
      true,
    );
    expect(
      normalizeSession({ ...base, needs_attention: true, activity_state: 'idle' }).needsAttention,
    ).toBe(true);
    expect(normalizeSession({ ...base, activity_state: 'active' }).needsAttention).toBe(false);
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

  it('getConversationHistory keeps who sent a user turn and none for older turns', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({
      turns: [
        { id: 'old', role: 'user', content: 'before authorship', created_at: '2026-11-01' },
        {
          id: 'new',
          role: 'user',
          content: 'hello',
          created_at: '2026-11-10',
          author: { user_id: 'u-alice', display_name: 'Alice Liddell' },
        },
      ],
    });

    const history = await buildVolundrHttpAdapter(client).getConversationHistory('sess-1');

    expect(history.turns[0]).not.toHaveProperty('author');
    expect(history.turns[1]?.author).toEqual({ user_id: 'u-alice', display_name: 'Alice Liddell' });
  });

  it('getConversationTurn calls GET /sessions/:id/conversation/turns/:turnId', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({
      turn: { id: 'turn/7', role: 'assistant', content: 'the reply', created_at: '2026-11-05' },
      index: 41,
      total_turns: 90,
    });

    const turn = await buildVolundrHttpAdapter(client).getConversationTurn('sess-1', 'turn/7');

    expect(client.get).toHaveBeenCalledWith('/sessions/sess-1/conversation/turns/turn%2F7');
    expect(turn).toMatchObject({ id: 'turn/7', role: 'assistant', content: 'the reply' });
  });

  it('getConversationTurn fails when Forge returns no turn', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({ index: null, total_turns: 0 });
    await expect(
      buildVolundrHttpAdapter(client).getConversationTurn('sess-1', 'gone'),
    ).rejects.toThrow('Forge returned no turn for gone');
  });

  it('getSession calls GET /sessions/:id', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).getSession('s1');
    expect(client.get).toHaveBeenCalledWith('/sessions/s1');
  });

  it('uses an explicit instance for a cold detail read and routes later mutations to it', async () => {
    const client = makeClient();
    client.get.mockResolvedValueOnce({
      id: 'shared-id',
      name: 'Build session',
      source: { type: 'local_mount', local_path: '/workspace' },
      status: 'running',
    });
    const service = buildVolundrHttpAdapter(client);

    await service.getSession('shared-id', { instanceId: 'build' });
    await service.stopSession('shared-id');
    await service.archiveSession('shared-id');

    expect(client.get).toHaveBeenCalledWith('/sessions/shared-id?instance_id=build');
    expect(client.post).toHaveBeenCalledWith('/sessions/shared-id/stop?instance_id=build');
    expect(client.patch).toHaveBeenCalledWith(
      '/sessions/shared-id/archive?instance_id=build',
      undefined,
    );
  });

  it('rejects a scoped detail response owned by a different instance', async () => {
    const client = makeClient();
    client.get.mockResolvedValueOnce({
      id: 'shared-id',
      name: 'Unexpected session',
      instance_id: 'thor',
      source: { type: 'local_mount', local_path: '/workspace' },
      status: 'running',
    });
    const service = buildVolundrHttpAdapter(client);

    await expect(service.getSession('shared-id', { instanceId: 'build' })).rejects.toThrow(
      'Session shared-id was returned by thor, expected build',
    );
    await service.stopSession('shared-id');

    expect(client.get).toHaveBeenCalledWith('/sessions/shared-id?instance_id=build');
    expect(client.post).toHaveBeenCalledWith('/sessions/shared-id/stop');
    expect(client.post).not.toHaveBeenCalledWith('/sessions/shared-id/stop?instance_id=thor');
  });

  it('does not fall back to a cached owner when an explicit instance has no matching session', async () => {
    const client = makeClient();
    client.get
      .mockResolvedValueOnce({
        id: 'shared-id',
        name: 'Thor session',
        instance_id: 'thor',
        source: { type: 'local_mount', local_path: '/workspace' },
        status: 'running',
      })
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce(null);
    const service = buildVolundrHttpAdapter(client);

    await service.getSession('shared-id', { instanceId: 'thor' });
    await expect(service.getSession('shared-id', { instanceId: 'missing' })).resolves.toBeNull();

    // Not found as a full session, it is looked for as a room view on the same host, and
    // nowhere else.
    expect(client.get).toHaveBeenCalledWith('/sessions/shared-id?instance_id=missing');
    expect(client.get).toHaveBeenLastCalledWith('/sessions/shared-id/room?instance_id=missing');
    expect(client.get).not.toHaveBeenCalledWith('/sessions/shared-id');
  });

  it('does not read a recorded question as a waiting session', async () => {
    const client = makeClient();
    client.get.mockResolvedValueOnce({
      id: 's1',
      name: 'n',
      source: { type: 'git', repo: '', branch: '' },
      status: 'running',
      model: 'm',
      activity_state: 'idle',
      activity_metadata: { help_needed: { reason: 'question' } },
    });

    expect((await buildVolundrHttpAdapter(client).getSession('s1')).needsAttention).toBe(false);
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

  it('getSession does not present a Ting workflow key as a ticket', async () => {
    const client = makeClient();
    client.get.mockResolvedValueOnce({
      id: 's1',
      name: 'tracker-delivery-flow',
      source: { type: 'git', repo: 'https://github.com/niuulabs/niuu', branch: 'regin' },
      status: 'running',
      model: 'gpt-5.6-terra',
      tracker_issue_id: 'workflow:a2a-509e14f61f0b48bfa43f05aab3bad1b9',
      issue_tracker_url: '',
    });

    const session = await buildVolundrHttpAdapter(client).getSession('s1');

    expect(session.trackerIssue).toBeUndefined();
    // It names the run it works for.
    expect(session.runId).toBe('a2a-509e14f61f0b48bfa43f05aab3bad1b9');
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

  it('getFeatures maps Forge flags and scopes requests to a target', async () => {
    const client = makeClient();
    vi.mocked(client.get).mockResolvedValue({
      local_mounts_enabled: true,
      file_manager_enabled: false,
      mini_mode: true,
    });
    expect(await buildVolundrHttpAdapter(client).getFeatures('host/a')).toEqual({
      localMountsEnabled: true,
      fileManagerEnabled: false,
      miniMode: true,
    });
    expect(client.get).toHaveBeenCalledWith('/feature-flags?instance_id=host%2Fa');
  });

  it('getLivePreviewConfig reads where previews open from Forge settings', async () => {
    const client = makeClient();
    vi.mocked(client.get).mockResolvedValue({
      enabled: true,
      origin_template: 'https://{port}--{session_id}.preview.niuu.world',
    });
    expect(await buildVolundrHttpAdapter(client).getLivePreviewConfig()).toEqual({
      enabled: true,
      originTemplate: 'https://{port}--{session_id}.preview.niuu.world',
    });
    expect(client.get).toHaveBeenCalledWith('/settings/live-preview');
  });

  it('getLivePreviewConfig asks the Forge host that runs the session', async () => {
    const client = makeClient();
    vi.mocked(client.get).mockResolvedValue({ enabled: false, origin_template: '' });
    expect(await buildVolundrHttpAdapter(client).getLivePreviewConfig('host/a')).toEqual({
      enabled: false,
      originTemplate: '',
    });
    expect(client.get).toHaveBeenCalledWith('/settings/live-preview?instance_id=host%2Fa');
  });

  it('getSessionRuntimes reads what the host runs every session as', async () => {
    const client = makeClient();
    vi.mocked(client.get).mockResolvedValue({
      runtimes: [
        {
          runtime: 'openshell',
          grants: ['sudo'],
          readiness: { state: 'boot', warm: 0, remedy: null },
        },
      ],
    });
    expect(await buildVolundrHttpAdapter(client).getSessionRuntimes('host/a')).toEqual([
      {
        runtime: 'openshell',
        grants: ['sudo'],
        readiness: { state: 'boot', warm: 0, remedy: null },
      },
    ]);
    expect(client.get).toHaveBeenCalledWith('/session-runtimes?instance_id=host%2Fa');
  });

  it('getSessionRuntimes refuses an answer that lists no runtimes', async () => {
    const client = makeClient();
    vi.mocked(client.get).mockResolvedValue([]);
    await expect(buildVolundrHttpAdapter(client).getSessionRuntimes('vms')).rejects.toThrow(
      'Invalid session runtimes from vms',
    );
  });

  it('getSessionRuntimes refuses a host that offers more than one runtime', async () => {
    const client = makeClient();
    const offer = {
      runtime: 'container',
      grants: [],
      readiness: { state: 'warm', warm: 1, remedy: null },
    };
    vi.mocked(client.get).mockResolvedValue({ runtimes: [offer, { ...offer, runtime: 'host' }] });
    await expect(buildVolundrHttpAdapter(client).getSessionRuntimes('vms')).rejects.toThrow(
      'vms offered 2 session runtimes; a host runs exactly one',
    );
    await expect(buildVolundrHttpAdapter(client).getSessionRuntimes()).rejects.toThrow(
      'The default host offered 2 session runtimes',
    );
  });

  it('getSessionRuntimes asks the default host without an instance', async () => {
    const client = makeClient();
    vi.mocked(client.get).mockResolvedValue({ runtimes: [] });
    expect(await buildVolundrHttpAdapter(client).getSessionRuntimes()).toEqual([]);
    expect(client.get).toHaveBeenCalledWith('/session-runtimes');
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
    const niuuClient = getDerivedClient('http://localhost:8080/api/v1/niuu');
    const credentialsClient = getDerivedClient('http://localhost:8080/api/v1/credentials');

    expect(catalogClient.get).toHaveBeenCalledWith('/session-definitions');
    expect(forgeClient.get).not.toHaveBeenCalledWith('/session-definitions');
    expect(
      (getDerivedClient('http://localhost:8080/api/v1/forge') ?? client).get,
    ).toHaveBeenCalledWith('/feature-flags');
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

    const niuuClient = getDerivedClient('http://localhost:8080/api/v1/niuu');

    expect(client.get).toHaveBeenCalledWith('/sessions/sess-1');
    expect(
      (getDerivedClient('http://localhost:8080/api/v1/forge') ?? client).get,
    ).toHaveBeenCalledWith('/feature-flags');
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
    expect(client.get).toHaveBeenCalledWith('/feature-flags');
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
        // the group key is the account that listed the repository
        account: 'GitHub',
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

  describe('node scope', () => {
    const REGISTRY = [
      {
        id: 'inst-valhalla',
        name: 'Valhalla',
        slug: 'valhalla',
        baseUrl: 'https://volundr.valhalla.asgard.niuu.world',
        visibility: 'system',
        enabled: true,
        isDefault: true,
        tags: ['valhalla', 'gpu'],
      },
      {
        id: 'inst-noatun',
        name: 'Noatun',
        slug: 'noatun',
        baseUrl: 'https://niuu.noatun.asgard.niuu.world',
        visibility: 'system',
        enabled: true,
        isDefault: false,
        tags: ['noatun', 'cpu'],
      },
    ];
    const NOATUN_MEMBERSHIP = {
      guild_url: 'https://yggdrasil.niuu.world',
      instance_id: 'inst-noatun',
      instance_name: 'Noatun',
      instance_slug: 'noatun',
    };

    function nodeAdapter(membership: typeof NOATUN_MEMBERSHIP) {
      const client = makeClientWithBase('https://niuu.noatun.asgard.niuu.world/api/v1/forge');
      client.get.mockImplementation(async (endpoint: string) =>
        endpoint === '/guild' ? membership : [],
      );
      const svc = buildVolundrHttpAdapter(client, undefined, {
        niuuBasePath: 'https://yggdrasil.niuu.world/api/v1/niuu',
        forgeScope: 'node',
      });
      const niuuClient = getDerivedClient('https://yggdrasil.niuu.world/api/v1/niuu')!;
      niuuClient.get.mockResolvedValue(REGISTRY);
      return { client, svc };
    }

    it('lists only the Forge node it is served from, as the registry describes it', async () => {
      const { client, svc } = nodeAdapter(NOATUN_MEMBERSHIP);

      const targets = await svc.getTargets();

      expect(client.get).toHaveBeenCalledWith('/guild');
      expect(targets).toEqual([
        expect.objectContaining({
          id: 'inst-noatun',
          name: 'Noatun',
          baseUrl: 'https://niuu.noatun.asgard.niuu.world',
          tags: ['noatun', 'cpu'],
        }),
      ]);
    });

    it('fails when the Guild registry does not list the node as an enabled Forge', async () => {
      const { svc } = nodeAdapter({ ...NOATUN_MEMBERSHIP, instance_id: 'inst-gone' });

      await expect(svc.getTargets()).rejects.toThrow(
        'This Forge node is Noatun (inst-gone) in the Guild at https://yggdrasil.niuu.world, ' +
          'but that Guild lists no enabled Forge with this id.',
      );
    });

    it('fails when the node cannot say which Forge it is', async () => {
      const { client, svc } = nodeAdapter(NOATUN_MEMBERSHIP);
      client.get.mockRejectedValue(new Error('This Forge node is not joined to a Guild'));

      await expect(svc.getTargets()).rejects.toThrow('This Forge node is not joined to a Guild');
    });

    it('lists every registered Forge in guild scope without asking the node', async () => {
      const client = makeClientWithBase('https://yggdrasil.niuu.world/api/v1/forge');
      const svc = buildVolundrHttpAdapter(client, undefined, {
        niuuBasePath: 'https://yggdrasil.niuu.world/api/v1/niuu',
        forgeScope: 'guild',
      });
      getDerivedClient('https://yggdrasil.niuu.world/api/v1/niuu')!.get.mockResolvedValue(REGISTRY);

      const targets = await svc.getTargets();

      expect(client.get).not.toHaveBeenCalledWith('/guild');
      expect(targets.map((target) => target.id)).toEqual(['inst-valhalla', 'inst-noatun']);
    });
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

  it('sendMessage posts to /sessions/:id/messages and reads a 200 as delivered', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({
      status: 'delivered',
      session_id: 's1',
      request_id: 'req-1',
      delivery: 'delivered',
    });
    const delivery = await buildVolundrHttpAdapter(client).sendMessage('s1', 'hello');
    expect(client.post).toHaveBeenCalledWith('/sessions/s1/messages', { content: 'hello' });
    expect(delivery).toEqual({ status: 'delivered', requestId: 'req-1', sessionId: 's1' });
  });

  it('sendMessage reads a 202 as pending, never as delivered', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({
      status: 'pending',
      session_id: 's1',
      request_id: 'req-2',
      delivery: 'pending',
    });
    const delivery = await buildVolundrHttpAdapter(client).sendMessage('s1', 'hello');
    expect(delivery.status).toBe('pending');
    expect(delivery.requestId).toBe('req-2');
  });

  it('sendMessage lets a refusal carry Forge detail to the caller', async () => {
    const client = makeClient();
    const refusal = Object.assign(new Error('API request failed: 404'), {
      status: 404,
      detail: 'Session s1 has no active endpoint',
    });
    client.post.mockRejectedValue(refusal);
    await expect(buildVolundrHttpAdapter(client).sendMessage('s1', 'hello')).rejects.toMatchObject({
      detail: 'Session s1 has no active endpoint',
    });
  });

  it.each([[{}], [{ status: 'unconfirmed', request_id: 'r' }], [{ status: 'delivered' }]])(
    'sendMessage refuses a body that does not say delivered or pending (%j)',
    async (body) => {
      const client = makeClient();
      client.post.mockResolvedValue(body);
      await expect(buildVolundrHttpAdapter(client).sendMessage('s1', 'hello')).rejects.toThrow(
        /Forge/,
      );
    },
  );

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
      'http://localhost:8080/api/v1/forge/sessions/stream?all_instances=true',
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

  it('applies session_activity events to the cached session', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([]);

    let onEvent: ((frame: { event?: string; data: string }) => void) | undefined;
    const openStream = vi.fn((_url: string, options: { onEvent?: typeof onEvent }) => {
      onEvent = options.onEvent;
      return { close: vi.fn() };
    });

    const svc = buildVolundrHttpAdapter(client, openStream as never);
    const seen: Array<Array<{ id: string; activityState?: string; needsAttention?: boolean }>> = [];
    svc.subscribe((sessions) => seen.push(sessions as never));
    await Promise.resolve();

    onEvent?.({
      event: 'session_updated',
      data: JSON.stringify({
        id: 'sess-1',
        name: 'alpha',
        source: { type: 'git', repo: 'r', branch: 'main' },
        status: 'running',
        model: 'm',
        activity_state: 'active',
      }),
    });
    // Unknown session is ignored (no throw, no phantom entry).
    onEvent?.({
      event: 'session_activity',
      data: JSON.stringify({ session_id: 'ghost', state: 'awaiting_input' }),
    });
    onEvent?.({
      event: 'session_activity',
      data: JSON.stringify({ session_id: 'sess-1', state: 'awaiting_input' }),
    });

    const last = seen.at(-1);
    expect(last).toHaveLength(1);
    expect(last?.[0]).toMatchObject({
      id: 'sess-1',
      activityState: 'awaiting_input',
      needsAttention: true,
    });
  });

  it('keeps remote state timestamps through SSE and stale REST responses, and rejects wrong-host events', async () => {
    const client = makeClient();
    const row = {
      id: 'remote',
      name: 'Remote',
      instance_id: 'bro',
      status: 'running',
      model: 'codex',
      activity_state: 'active',
      activity_state_since: '2026-09-19T12:00:00Z',
      turn_started_at: '2026-09-19T12:00:00Z',
    };
    client.get.mockResolvedValue([row]);
    let onEvent!: (frame: { event?: string; data: string }) => void;
    const open = vi.fn((_url: string, options: { onEvent?: typeof onEvent }) => {
      onEvent = options.onEvent!;
      return { close: vi.fn() };
    });
    const svc = buildVolundrHttpAdapter(client, open as never);
    const seen = vi.fn();
    const stop = svc.subscribe(seen, { hydrate: false });
    expect(client.get).not.toHaveBeenCalled();
    await svc.getSessions({ instanceId: 'bro' });
    const send = (state: string, since: string, instance_id = 'bro') =>
      onEvent({
        event: 'session_activity',
        data: JSON.stringify({
          session_id: 'remote',
          instance_id,
          state,
          activity_state_since: since,
          turn_started_at: null,
        }),
      });
    send('idle', '2026-09-19T12:02:00Z');
    expect(seen.mock.lastCall?.[0][0]).toMatchObject({
      activityState: 'idle',
      activityStateSince: '2026-09-19T12:02:00Z',
      turnStartedAt: null,
    });
    send('active', '2026-09-19T12:00:00Z');
    send('active', '2026-09-19T12:03:00Z', 'thor');
    expect(seen.mock.lastCall?.[0][0].activityState).toBe('idle');
    expect((await svc.getSessions({ instanceId: 'bro' }))[0]).toMatchObject({
      activityState: 'idle',
      turnStartedAt: null,
    });
    stop();
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

it('normalizes Settings integration fields for session defaults', async () => {
  const service = buildVolundrHttpAdapter(makeClient());
  const sharedClient = getDerivedClient('http://localhost:8080/api/v1');
  sharedClient.get.mockResolvedValueOnce([
    {
      id: 'github',
      slug: 'github',
      enabled: true,
      integration_type: 'source_control',
      credential_name: 'github-credential',
      created_at: 'created',
      updated_at: 'updated',
    },
  ]);
  expect(await service.getIntegrations()).toEqual([
    {
      id: 'github',
      slug: 'github',
      enabled: true,
      integrationType: 'source_control',
      credentialName: 'github-credential',
      createdAt: 'created',
      updatedAt: 'updated',
    },
  ]);
});

it('routes personal home operations with encoded cluster and relative path', async () => {
  const client = makeClient();
  const service = buildVolundrHttpAdapter(client);
  await service.listUserHome('cluster-a', 'tmp/cache');
  expect(client.get).toHaveBeenCalledWith('/storage/home?instance_id=cluster-a&path=tmp%2Fcache');
  await service.deleteUserHomePath('cluster-b', 'tmp/a b');
  expect(client.delete).toHaveBeenCalledWith('/storage/home?instance_id=cluster-b&path=tmp%2Fa+b');
});

describe('session resource byte downloads', () => {
  it('preserves bytes, bearer auth, escaped file identifiers and abort signals', async () => {
    const fetchImpl = vi.fn().mockImplementation(
      async () =>
        new Response(new Uint8Array([0, 255, 13, 10]), {
          headers: { 'Content-Type': 'image/png' },
        }),
    );
    const fs = buildVolundrFileSystemHttpAdapter({
      baseUrl: 'https://thor.test/api/v1/forge',
      fetchImpl,
    });
    const signal = new AbortController().signal;
    const file = await fs.downloadFile!('session-1', '/workspace/docs/a b.png', signal);
    expect(new Uint8Array(await file.arrayBuffer())).toEqual(new Uint8Array([0, 255, 13, 10]));
    expect(file.type).toBe('image/png');
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      'https://thor.test/api/v1/forge/sessions/session-1/files/download?root=workspace&path=docs%2Fa+b.png',
    );
    expect(fetchImpl.mock.calls[0]?.[1].signal).toBe(signal);
    expect(fetchImpl.mock.calls[0]?.[1].headers.get('Authorization')).toBe('Bearer token-123');
    await fs.downloadPresentedFile!('session-1', 'id/with spaces', signal);
    expect(fetchImpl.mock.calls[1]?.[0]).toBe(
      'https://thor.test/api/v1/forge/sessions/session-1/files/presented/id%2Fwith%20spaces',
    );
  });
  it('propagates failed or cancelled downloads', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(new Response('File not found', { status: 404 }))
      .mockRejectedValueOnce(new DOMException('Cancelled', 'AbortError'));
    const fs = buildVolundrFileSystemHttpAdapter({
      baseUrl: 'https://thor.test/api/v1/forge',
      fetchImpl,
    });
    await expect(fs.downloadFile!('s', '/workspace/missing')).rejects.toThrow();
    await expect(fs.downloadPresentedFile!('s', 'missing')).rejects.toMatchObject({
      name: 'AbortError',
    });
  });
});

describe('Forge project grouping contract', () => {
  it('creates a project by name, on a chosen host when one is given', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({ id: 'new' });
    const service = buildVolundrHttpAdapter(client);
    await service.createProject('Shop');
    expect(client.post).toHaveBeenLastCalledWith('/projects', { name: 'Shop' });
    await service.createProject('Shop', { instanceId: 'ymir' });
    expect(client.post).toHaveBeenLastCalledWith('/projects', {
      name: 'Shop',
      instance_id: 'ymir',
    });
  });

  it('updates a project at its revision on the host that holds it', async () => {
    const client = makeClient();
    client.patch.mockResolvedValue({ id: 'lexi' });
    const service = buildVolundrHttpAdapter(client);
    const project = {
      id: 'lexi',
      name: 'Lexi',
      slug: 'lexi',
      status: 'active' as const,
      revision: 4,
      instance_id: 'ymir',
    };
    await service.updateProject(project, { brief: 'New brief' });
    expect(client.patch).toHaveBeenCalledWith('/projects/lexi?instance_id=ymir', {
      brief: 'New brief',
      revision: 4,
    });
    await service.updateProject({ ...project, instance_id: undefined }, { repo_url: '' });
    expect(client.patch).toHaveBeenLastCalledWith('/projects/lexi', { repo_url: '', revision: 4 });
  });

  it('reaches project members on the host that owns the project', async () => {
    const client = makeClient();
    const service = buildVolundrHttpAdapter(client);
    const project = { id: 'lexi', name: 'Lexi', slug: 'lexi', status: 'active' as const };
    const remote = { ...project, instance_id: 'ymir' };
    await service.getProjectMembers(project);
    expect(client.get).toHaveBeenLastCalledWith('/projects/lexi/members');
    await service.getProjectMemberCandidates(remote, 'ali');
    expect(client.get).toHaveBeenLastCalledWith(
      '/projects/lexi/member-candidates?q=ali&instance_id=ymir',
    );
    await service.addProjectMember(project, { userId: 'alice', role: 'approver' });
    expect(client.post).toHaveBeenLastCalledWith('/projects/lexi/members', {
      user_id: 'alice',
      role: 'approver',
    });
    await service.addProjectMember(project, { userId: 'bram', role: 'viewer', expertise: 'Ting' });
    expect(client.post).toHaveBeenLastCalledWith('/projects/lexi/members', {
      user_id: 'bram',
      role: 'viewer',
      expertise: 'Ting',
    });
    await service.updateProjectMember(remote, 'a b', { role: 'owner' });
    expect(client.patch).toHaveBeenLastCalledWith('/projects/lexi/members/a%20b?instance_id=ymir', {
      role: 'owner',
    });
    await service.removeProjectMember(project, 'alice');
    expect(client.delete).toHaveBeenLastCalledWith('/projects/lexi/members/alice');
  });

  it('reaches a project\u2019s team integrations on the host that owns the project', async () => {
    const client = makeClient();
    const service = buildVolundrHttpAdapter(client);
    const project = { id: 'lexi', name: 'Lexi', slug: 'lexi', status: 'active' as const };
    const remote = { ...project, instance_id: 'ymir' };
    await service.getTeamIntegrationCatalog(project);
    expect(client.get).toHaveBeenLastCalledWith('/projects/lexi/integrations/catalog');
    await service.getTeamIntegrations(remote);
    expect(client.get).toHaveBeenLastCalledWith('/projects/lexi/integrations?instance_id=ymir');
    await service.getTeamRoutes(project);
    expect(client.get).toHaveBeenLastCalledWith('/projects/lexi/integrations/routes');
    await service.getTeamIntegrationUsage(project);
    expect(client.get).toHaveBeenLastCalledWith('/projects/lexi/integrations/usage');
    await service.getTeamIntegrationUsage(project, '2026-11-01T00:00:00Z');
    expect(client.get).toHaveBeenLastCalledWith(
      '/projects/lexi/integrations/usage?before=2026-11-01T00%3A00%3A00Z',
    );
    const connect = {
      slug: 'jira',
      credential: { client_id: 'id', client_secret: 'secret' },
      config: { site_url: 'https://acme.atlassian.net', cloud_id: 'c1' },
    };
    await service.connectTeamServiceAccount(project, connect);
    expect(client.post).toHaveBeenLastCalledWith('/projects/lexi/integrations', connect);
    await service.startTeamOAuth(project, 'slack', { channel: '#eng' });
    expect(client.post).toHaveBeenLastCalledWith('/integrations/oauth/authorize-team', {
      slug: 'slack',
      project_id: 'lexi',
      config: { channel: '#eng' },
    });
    await service.updateTeamIntegration(project, 'c 1', { enabled: false });
    expect(client.patch).toHaveBeenLastCalledWith('/projects/lexi/integrations/c%201', {
      enabled: false,
    });
    await service.removeTeamIntegration(project, 'c1');
    expect(client.delete).toHaveBeenLastCalledWith('/projects/lexi/integrations/c1');
    const route = {
      connection_id: 'c1',
      deliver_to: 'channel' as const,
      channel: '#eng',
      enabled: true,
    };
    await service.setTeamRoute(project, 'errors', route);
    expect(client.put).toHaveBeenLastCalledWith('/projects/lexi/integrations/routes/errors', route);
    await service.removeTeamRoute(project, 'errors');
    expect(client.delete).toHaveBeenLastCalledWith('/projects/lexi/integrations/routes/errors');
  });

  it('sends a description, a status and a checkout folder as the project’s own fields', async () => {
    const client = makeClient();
    client.patch.mockResolvedValue({ id: 'lexi' });
    const service = buildVolundrHttpAdapter(client);
    await service.updateProject(
      { id: 'lexi', name: 'Lexi', slug: 'lexi', status: 'active', revision: 4 },
      { description: 'Chat', status: 'archived', workspace_path: '/work/lexi' },
    );
    expect(client.patch).toHaveBeenCalledWith('/projects/lexi', {
      description: 'Chat',
      status: 'archived',
      workspace_path: '/work/lexi',
      revision: 4,
    });
  });

  it('loads projects from Forge and preserves coordination and parent references on sessions', async () => {
    const client = makeClient();
    const project = { id: 'lexi', name: 'Lexi', slug: 'lexi', status: 'active' };
    client.get.mockResolvedValue([project]);
    const service = buildVolundrHttpAdapter(client);
    expect(await service.getProjects()).toEqual([project]);
    expect(client.get).toHaveBeenCalledWith('/projects');
    const normalized = __testables.normalizeSession({
      id: 'worker',
      name: 'iOS',
      source: { type: 'git', repo: 'lexi', branch: 'main' },
      status: 'running',
      model: 'astra',
      coordination: {
        project_id: 'lexi',
        role: 'worker',
        parent: { instance_id: 'thor', session_id: 'coordinator' },
        objective: 'Remove retired models',
        labels: ['work-project:p1'],
      },
    });
    expect(normalized.coordination).toEqual({
      projectId: 'lexi',
      role: 'worker',
      parent: { instanceId: 'thor', sessionId: 'coordinator' },
      objective: 'Remove retired models',
      labels: ['work-project:p1'],
    });
    client.get.mockRejectedValue(new Error('host offline'));
    await expect(service.getProjects()).rejects.toThrow('host offline');
  });
});

it('isolates host inventory reads, preserves other cached hosts, and routes detail to its owner', async () => {
  const client = makeClient();
  const payload = (id: string, instance: string) => ({
    id,
    name: id,
    instance_id: instance,
    status: 'running',
    source: { type: 'local_mount', local_path: '/workspace' },
  });
  client.get.mockImplementation(async (path: string) =>
    path.startsWith('/sessions/a?')
      ? payload('a', 'thor')
      : path.includes('instance_id=thor')
        ? [payload('a', 'thor')]
        : path.includes('instance_id=build')
          ? [payload('b', 'build')]
          : [],
  );
  const service = buildVolundrHttpAdapter(client);
  const controller = new AbortController();
  await service.getSessions({ instanceId: 'thor', signal: controller.signal });
  await service.getSessions({ instanceId: 'build' });
  expect(client.get).toHaveBeenCalledWith('/sessions?instance_id=thor', {
    signal: controller.signal,
  });
  await service.getSession('a');
  expect(client.get).toHaveBeenCalledWith('/sessions/a?instance_id=thor');
  await service.listArchivedSessions({ instanceId: 'build', signal: controller.signal });
  expect(client.get).toHaveBeenCalledWith('/sessions?status=archived&instance_id=build', {
    signal: controller.signal,
  });
});

it('routes archived detail directly to its host and removes obsolete archive owners on refresh', async () => {
  const client = makeClient();
  const archived = {
    id: 'old',
    name: 'Archived',
    instance_id: 'thor',
    status: 'archived',
    source: { type: 'local_mount', local_path: '/workspace' },
  };
  client.get.mockResolvedValue([archived]);
  const service = buildVolundrHttpAdapter(client);
  await service.listArchivedSessions({ instanceId: 'thor' });
  client.get.mockResolvedValue(null);
  await service.getSession('old');
  expect(client.get).toHaveBeenCalledWith('/sessions/old?instance_id=thor');
  expect(client.get).toHaveBeenLastCalledWith('/sessions/old/room?instance_id=thor');
  client.get.mockResolvedValue([]);
  await service.listArchivedSessions({ instanceId: 'build' });
  await service.listArchivedSessions({ instanceId: 'thor' });
  client.get.mockResolvedValue(null);
  await service.getSession('old');
  expect(client.get).toHaveBeenCalledWith('/sessions/old');
  expect(client.get).toHaveBeenLastCalledWith('/sessions/old/room');
});

it('scopes metrics and resources, forwards cancellation, and rejects gateways that ignore the host', async () => {
  const client = makeClient();
  const service = buildVolundrHttpAdapter(client);
  const signal = new AbortController().signal;
  client.get.mockResolvedValue({ instance_id: 'thor', tokens_today: 123 });
  await expect(service.getStats({ instanceId: 'thor', signal })).resolves.toMatchObject({
    tokensToday: 123,
  });
  expect(client.get).toHaveBeenLastCalledWith('/stats?instance_id=thor', { signal });
  client.get.mockResolvedValue({ instances: [{ id: 'thor' }], nodes: [] });
  await expect(service.getClusterResources({ instanceId: 'thor', signal })).resolves.toMatchObject({
    nodes: [],
  });
  expect(client.get).toHaveBeenLastCalledWith('/cluster/resources?instance_id=thor', { signal });
  client.get.mockResolvedValue({ tokens_today: 999, instances: [{ id: 'thor' }, { id: 'build' }] });
  await expect(service.getStats({ instanceId: 'thor' })).rejects.toThrow(
    'Update the Forge gateway',
  );
  await expect(service.getClusterResources({ instanceId: 'thor' })).rejects.toThrow(
    'Update the Forge gateway',
  );
  client.get.mockResolvedValue({ instances: [{ id: 'build' }] });
  await expect(service.getClusterResources({ instanceId: 'thor' })).rejects.toThrow(
    'Update the Forge gateway',
  );
  client.get.mockResolvedValue({ tokens_today: 5 });
  await expect(service.getStats({ signal })).resolves.toMatchObject({ tokensToday: 5 });
  expect(client.get).toHaveBeenLastCalledWith('/stats', { signal });
  await service.getClusterResources({ signal });
  expect(client.get).toHaveBeenLastCalledWith('/cluster/resources', { signal });
});

describe('shared Forge stream listeners', () => {
  type StreamOptions = {
    onEvent?: (frame: { event?: string; data: string }) => void;
    onOpen?: () => void;
    onError?: (error: unknown) => void;
  };

  function streamHarness() {
    let options: StreamOptions = {};
    const close = vi.fn();
    const openStream = vi.fn((_url: string, next: StreamOptions) => {
      options = next;
      return { close };
    });
    return { openStream, close, options: () => options };
  }

  it('hands raw events and connection status to feature listeners on one stream', async () => {
    const client = makeClient();
    const stream = streamHarness();
    const svc = buildVolundrHttpAdapter(client, stream.openStream as never);
    const sessions: Array<Array<{ id: string }>> = [];
    const unsubscribeSessions = svc.subscribe((next) => sessions.push(next), { hydrate: false });
    const events: Array<{ type: string; data: unknown }> = [];
    const statuses: string[] = [];
    const stop = svc.subscribeForgeStream({
      onEvent: (event) => events.push(event),
      onStatus: (status) => statuses.push(status),
    });
    expect(stream.openStream).toHaveBeenCalledTimes(1);

    stream.options().onOpen?.();
    const notification = { id: 'n-1', seq: 3, title: 'Done', instance_id: 'thor' };
    stream.options().onEvent?.({
      event: 'session_notification',
      data: JSON.stringify(notification),
    });
    stream.options().onError?.(new Error('dropped'));

    expect(events).toEqual([{ type: 'session_notification', data: notification }]);
    expect(statuses).toEqual(['open', 'error']);
    // A notification carries an id but must never touch the session cache.
    expect(sessions.every((snapshot) => snapshot.length === 0)).toBe(true);

    // A listener that joins while the stream is known reports its state immediately.
    const late: string[] = [];
    const stopLate = svc.subscribeForgeStream({
      onEvent: () => {
        throw new Error('listener bug');
      },
      onStatus: (status) => late.push(status),
    });
    expect(late).toEqual(['error']);
    stream
      .options()
      .onEvent?.({ event: 'session_notification', data: JSON.stringify(notification) });
    expect(events).toHaveLength(2);

    unsubscribeSessions();
    stopLate();
    expect(stream.close).not.toHaveBeenCalled();
    stop();
    expect(stream.close).toHaveBeenCalledTimes(1);
  });

  it('re-reads a cached session when its read state changes and ignores unknown sessions', async () => {
    const client = makeClient();
    client.get.mockImplementation(async (endpoint: string) => {
      if (endpoint === '/sessions')
        return [{ id: 'sess-1', name: 'alpha', status: 'running', model: 'opus' }];
      if (endpoint.startsWith('/sessions/sess-1'))
        return {
          id: 'sess-1',
          name: 'alpha',
          status: 'running',
          model: 'opus',
          read_state: { is_unread: false, latest_output_seq: 9, read_through_seq: 9, revision: 2 },
        };
      return [];
    });
    const stream = streamHarness();
    const svc = buildVolundrHttpAdapter(client, stream.openStream as never);
    const seen: Array<Array<{ id: string; readState?: unknown }>> = [];
    svc.subscribe((sessions) => seen.push(sessions));
    await vi.waitFor(() => expect(seen.at(-1)).toHaveLength(1));

    stream.options().onEvent?.({
      event: 'session_read_state',
      data: JSON.stringify({ session_id: 'sess-1', owner_id: 'user-1' }),
    });
    // A second hint while the first read is in flight is coalesced.
    stream.options().onEvent?.({
      event: 'session_read_state',
      data: JSON.stringify({ session_id: 'sess-1', owner_id: 'user-1' }),
    });
    stream.options().onEvent?.({
      event: 'session_read_state',
      data: JSON.stringify({ session_id: 'unknown', owner_id: 'user-1' }),
    });
    stream.options().onEvent?.({ event: 'session_read_state', data: JSON.stringify({}) });

    await vi.waitFor(() =>
      expect(seen.at(-1)?.[0]?.readState).toEqual({
        isUnread: false,
        latestOutputSeq: 9,
        latestOutputTurnId: null,
        latestOutputAt: null,
        readThroughSeq: 9,
        manuallyUnread: false,
        revision: 2,
      }),
    );
    const sessionReads = client.get.mock.calls.filter(([endpoint]) =>
      String(endpoint).startsWith('/sessions/'),
    );
    expect(sessionReads).toHaveLength(1);
  });
});

describe('session read state', () => {
  const payload = {
    is_unread: true,
    latest_output_seq: 14,
    latest_output_turn_id: 'turn-9',
    latest_output_at: '2026-11-05T10:00:00Z',
    read_through_seq: 9,
    manually_unread: false,
    revision: 3,
  };
  const row = {
    id: 'sess-1',
    name: 'alpha',
    status: 'running',
    model: 'opus',
    instance_id: 'thor',
  };

  it('reads the state of a session on its own host', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(payload);
    const state = await buildVolundrHttpAdapter(client).getSessionReadState('sess-1', {
      instanceId: 'thor',
    });
    expect(client.get).toHaveBeenCalledWith('/sessions/sess-1/read-state?instance_id=thor', {
      signal: undefined,
    });
    expect(state).toEqual({
      isUnread: true,
      latestOutputSeq: 14,
      latestOutputTurnId: 'turn-9',
      latestOutputAt: '2026-11-05T10:00:00Z',
      readThroughSeq: 9,
      manuallyUnread: false,
      revision: 3,
    });
  });

  it('sends the revision and the output seen, and publishes the new state to subscribers', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([row]);
    client.patch.mockResolvedValue({
      ...payload,
      is_unread: false,
      read_through_seq: 14,
      revision: 4,
    });
    const svc = buildVolundrHttpAdapter(client, vi.fn(() => ({ close: vi.fn() })) as never);
    await svc.getSessions();
    const seen: Array<Array<{ readState?: { isUnread: boolean; revision: number } }>> = [];
    svc.subscribe((sessions) => seen.push(sessions), { hydrate: false });

    const next = await svc.changeSessionReadState('sess-1', {
      state: 'read',
      expectedRevision: 3,
      throughSeq: 14,
    });

    expect(client.patch).toHaveBeenCalledWith('/sessions/sess-1/read-state?instance_id=thor', {
      state: 'read',
      expected_revision: 3,
      through_seq: 14,
    });
    expect(next).toMatchObject({ isUnread: false, readThroughSeq: 14, revision: 4 });
    expect(seen.at(-1)?.[0]?.readState).toMatchObject({ isUnread: false, revision: 4 });
  });

  it('turns a 409 into a conflict the caller must look at', async () => {
    const client = makeClient();
    client.patch.mockRejectedValue(new ApiClientError('Conflict', 409, 'Read state changed'));
    const change = buildVolundrHttpAdapter(client).changeSessionReadState('sess-1', {
      state: 'unread',
      expectedRevision: 1,
      throughSeq: 2,
    });
    await expect(change).rejects.toBeInstanceOf(SessionReadStateConflictError);
    await expect(change).rejects.toThrow('Read state changed');
  });

  it('does not disguise any other failure as a conflict', async () => {
    const client = makeClient();
    client.patch.mockRejectedValue(new ApiClientError('Forbidden', 403, 'Access denied'));
    await expect(
      buildVolundrHttpAdapter(client).changeSessionReadState('sess-1', {
        state: 'read',
        expectedRevision: 0,
        throughSeq: 0,
      }),
    ).rejects.toMatchObject({ status: 403 });
  });

  it('maps the agent summary and the 24-hour work total from the list', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([
      { ...row, short_description: 'Tests next', worked_seconds_24h: 3600, read_state: payload },
      { id: 'sess-2', name: 'beta', status: 'running', model: 'opus', worked_seconds_24h: null },
    ]);
    const [first, second] = await buildVolundrHttpAdapter(client).getSessions();
    expect(first).toMatchObject({
      shortDescription: 'Tests next',
      workedSeconds24h: 3600,
      readState: { isUnread: true, latestOutputTurnId: 'turn-9' },
    });
    expect(second).not.toHaveProperty('workedSeconds24h');
    expect(second).not.toHaveProperty('shortDescription');
  });

  it('keeps the caller-specific fields when a stream event omits them', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([{ ...row, worked_seconds_24h: 600, read_state: payload }]);
    let streamOptions: { onEvent?: (event: { event?: string; data: string }) => void } = {};
    const svc = buildVolundrHttpAdapter(
      client,
      vi.fn((_url: string, next: typeof streamOptions) => {
        streamOptions = next;
        return { close: vi.fn() };
      }) as never,
    );
    await svc.getSessions();
    const seen: Array<Array<Record<string, unknown>>> = [];
    svc.subscribe((sessions) => seen.push(sessions as never), { hydrate: false });

    streamOptions.onEvent?.({
      event: 'session_updated',
      data: JSON.stringify({ ...row, name: 'renamed' }),
    });

    expect(seen.at(-1)?.[0]).toMatchObject({
      name: 'renamed',
      workedSeconds24h: 600,
      readState: { isUnread: true, revision: 3 },
    });
  });

  it('sends only the Forge tool grants that were asked for', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({ id: 'sess-9', name: 'n', status: 'starting', model: 'm' });
    const svc = buildVolundrHttpAdapter(client);
    const base = {
      name: 'n',
      model: 'm',
      source: { type: 'git' as const, repo: 'r', branch: 'main' },
    };
    await svc.startSession({ ...base, forgeMcp: { grants: ['message', 'lifecycle'] } });
    expect(client.post.mock.calls.at(-1)?.[1]).toMatchObject({
      forge_mcp: { grants: ['message', 'lifecycle'] },
    });
    await svc.startSession({ ...base, forgeMcp: { grants: [] } });
    expect(client.post.mock.calls.at(-1)?.[1]).not.toHaveProperty('forge_mcp');
    await svc.startSession(base);
    expect(client.post.mock.calls.at(-1)?.[1]).not.toHaveProperty('forge_mcp');
  });
});

describe('secret requests', () => {
  const payload = {
    id: 'r 1',
    session_id: 'sess-1',
    name: 'GITLAB_TOKEN',
    purpose: 'Push the branch',
    target: 'gitlab.niuu.world',
    status: 'fulfilled',
    keep: 'saved',
    credential_name: 'gitlab-token',
    created_at: '2026-11-06T10:00:00Z',
    resolved_at: '2026-11-06T10:01:00Z',
    delivered_at: null,
  };

  it('reads a request from the session secret-requests route', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(payload);
    const request = await buildVolundrHttpAdapter(client).getSecretRequest('sess-1', 'r 1');
    expect(client.get).toHaveBeenCalledWith('/sessions/sess-1/secret-requests/r%201');
    expect(request).toEqual({
      id: 'r 1',
      sessionId: 'sess-1',
      name: 'GITLAB_TOKEN',
      purpose: 'Push the branch',
      target: 'gitlab.niuu.world',
      status: 'fulfilled',
      keep: 'saved',
      credentialName: 'gitlab-token',
    });
  });

  it('posts only the value and keep choice to fulfill and returns no value', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({ ...payload, keep: 'session', credential_name: null });
    const request = await buildVolundrHttpAdapter(client).fulfillSecretRequest('sess-1', 'r-1', {
      value: 'glpat-123',
      keep: 'session',
    });
    expect(client.post).toHaveBeenCalledExactlyOnceWith(
      '/sessions/sess-1/secret-requests/r-1/fulfill',
      { value: 'glpat-123', keep: 'session' },
    );
    expect(request).toMatchObject({ status: 'fulfilled', keep: 'session', credentialName: null });
    expect(JSON.stringify(request)).not.toContain('glpat-123');
  });

  it('posts a decline with no body', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({
      ...payload,
      status: 'declined',
      keep: null,
      target: undefined,
    });
    const request = await buildVolundrHttpAdapter(client).declineSecretRequest('sess-1', 'r-1');
    expect(client.post).toHaveBeenCalledExactlyOnceWith(
      '/sessions/sess-1/secret-requests/r-1/decline',
    );
    expect(request).toMatchObject({ status: 'declined', keep: null, target: null });
  });

  it('lets a Forge error reach the caller', async () => {
    const client = makeClient();
    client.post.mockRejectedValue(new Error('409 already fulfilled'));
    await expect(
      buildVolundrHttpAdapter(client).fulfillSecretRequest('sess-1', 'r-1', {
        value: 'v',
        keep: 'saved',
      }),
    ).rejects.toThrow('409 already fulfilled');
  });

  it('refuses a status or keep choice it does not know', async () => {
    const client = makeClient();
    const adapter = buildVolundrHttpAdapter(client);
    client.get.mockResolvedValueOnce({ ...payload, status: 'mystery' });
    await expect(adapter.getSecretRequest('sess-1', 'r-1')).rejects.toThrow(
      'unknown secret request status: mystery',
    );
    client.get.mockResolvedValueOnce({ ...payload, keep: 'forever' });
    await expect(adapter.getSecretRequest('sess-1', 'r-1')).rejects.toThrow(
      'unknown secret keep choice: forever',
    );
  });
});

describe('sessions shared through a room', () => {
  const summary = {
    kind: 'room_summary',
    id: 'shared-1',
    name: 'Shared work',
    status: 'running',
    owner_id: 'alice',
    room: { role: 'approver', participant_status: 'active', expires_at: null },
    coordination: { project_id: 'proj-1', role: 'worker' },
    model: 'claude-sonnet-4-6',
    chat_endpoint: 'ws://forge.test/s/shared-1/session',
    last_active: '2026-11-10T10:00:00Z',
    activity_state: 'awaiting_input',
    needs_attention: true,
    instance_id: 'thor',
    instance_name: 'Thor',
  };

  it('reads a room summary without a workspace and never throws on it', () => {
    const session = __testables.normalizeSession(summary as never);

    expect(session.source).toBeNull();
    expect(session.room).toEqual({
      role: 'approver',
      participantStatus: 'active',
      expiresAt: null,
    });
    expect(session).toMatchObject({
      id: 'shared-1',
      ownerId: 'alice',
      model: 'claude-sonnet-4-6',
      chatEndpoint: 'ws://forge.test/s/shared-1/session',
      activityState: 'awaiting_input',
      needsAttention: true,
      instanceId: 'thor',
      coordination: { projectId: 'proj-1', role: 'worker', labels: [] },
    });
    expect(session.lastActive).toBe(Date.parse('2026-11-10T10:00:00Z'));
    expect(session.codeEndpoint).toBeUndefined();
    expect(session.podName).toBeUndefined();
  });

  it('keeps a full session free of a room', () => {
    const session = __testables.normalizeSession({
      id: 's1',
      name: 'mine',
      status: 'running',
      model: 'm',
      source: { type: 'git', repo: 'r', branch: 'b' },
    } as never);

    expect(session.room).toBeUndefined();
    expect(session.source).toEqual({ type: 'git', repo: 'r', branch: 'b' });
  });

  it("lists shared sessions beside the caller's own", async () => {
    const client = makeClient();
    client.get.mockResolvedValue([
      { id: 'own', name: 'own', status: 'running', model: 'm', source: { type: 'git', repo: 'r' } },
      summary,
    ]);

    const sessions = await buildVolundrHttpAdapter(client).getSessions();

    expect(sessions.map((s) => s.id)).toEqual(['own', 'shared-1']);
    expect(sessions[1]?.room?.role).toBe('approver');
  });

  it('leaves out one unreadable entry loudly instead of failing the whole list', async () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const client = makeClient();
    client.get.mockResolvedValue([
      null,
      { id: 'own', name: 'own', status: 'running', model: 'm', source: { type: 'git', repo: 'r' } },
      summary,
    ]);

    const sessions = await buildVolundrHttpAdapter(client).getSessions();

    expect(sessions.map((s) => s.id)).toEqual(['own', 'shared-1']);
    expect(errors).toHaveBeenCalledTimes(1);
    expect(errors.mock.calls[0]?.[0]).toContain('could not be read');
    errors.mockRestore();
  });

  it('reads a known shared session through its room view, not the refused full read', async () => {
    const client = makeClient();
    client.get.mockResolvedValueOnce([summary]);
    const service = buildVolundrHttpAdapter(client);
    await service.getSessions();
    client.get.mockClear();
    client.get.mockResolvedValue({ ...summary, name: 'Renamed' });

    const session = await service.getSession('shared-1');

    expect(client.get).toHaveBeenCalledTimes(1);
    expect(client.get).toHaveBeenCalledWith('/sessions/shared-1/room?instance_id=thor');
    expect(session?.name).toBe('Renamed');
    expect(session?.room?.role).toBe('approver');
  });

  it('answers a refused full read with the room view', async () => {
    const client = makeClient();
    client.get
      .mockRejectedValueOnce(new ApiClientError('Forbidden', 403))
      .mockResolvedValueOnce(summary);

    const session = await buildVolundrHttpAdapter(client).getSession('shared-1');

    expect(client.get).toHaveBeenNthCalledWith(1, '/sessions/shared-1');
    expect(client.get).toHaveBeenNthCalledWith(2, '/sessions/shared-1/room');
    expect(session?.room?.role).toBe('approver');
  });

  it('does not hide any other failure of the full read behind the room view', async () => {
    const client = makeClient();
    client.get.mockRejectedValueOnce(new ApiClientError('Server error', 500));

    await expect(buildVolundrHttpAdapter(client).getSession('shared-1')).rejects.toThrow(
      'Server error',
    );
    expect(client.get).toHaveBeenCalledTimes(1);
  });

  it("reads the owner's room view as the owner's, not as a shared session", async () => {
    const client = makeClient();
    client.get.mockResolvedValueOnce([
      { ...summary, room: { role: 'owner', participant_status: 'active' } },
    ]);
    const service = buildVolundrHttpAdapter(client);
    const [listed] = await service.getSessions();
    expect(listed?.room?.role).toBe('owner');
    client.get.mockClear();
    client.get.mockResolvedValue({ id: 'shared-1', name: 'full', status: 'running', model: 'm' });

    await service.getSession('shared-1');

    expect(client.get).toHaveBeenCalledWith('/sessions/shared-1?instance_id=thor');
  });
});
