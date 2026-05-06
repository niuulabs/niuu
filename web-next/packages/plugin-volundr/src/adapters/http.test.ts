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
}));

vi.mock('@niuulabs/query', async () => {
  const actual = await vi.importActual<typeof import('@niuulabs/query')>('@niuulabs/query');
  return {
    ...actual,
    createApiClient: queryMocks.createApiClient,
    getAccessToken: queryMocks.getAccessToken,
  };
});

import { buildVolundrFileSystemHttpAdapter, buildVolundrHttpAdapter } from './http';
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
      'https://sessions.example.com/s/sess-1/api/files?root=workspace',
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
      },
    ]);
  });

  it('getRepos uses the shared niuu repo catalog and normalizes grouped provider payloads', async () => {
    const client = makeClientWithBase('http://localhost:8080/api/v1/volundr');
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

  it('startSession calls POST /sessions', async () => {
    const client = makeClient();
    const config = {
      name: 'test',
      source: { type: 'git' as const, repo: 'r', branch: 'main' },
      model: 'claude-sonnet',
    };
    await buildVolundrHttpAdapter(client).startSession(config);
    expect(client.post).toHaveBeenCalledWith('/sessions', config);
  });

  it('derives a canonical forge client for session launch when the main base is legacy volundr', async () => {
    const client = makeClientWithBase('http://localhost:8080/api/v1/volundr');
    const config = {
      name: 'test',
      source: { type: 'git' as const, repo: 'r', branch: 'main' },
      model: 'claude-sonnet',
    };

    await buildVolundrHttpAdapter(client).startSession(config);

    const forgeClient = getDerivedClient('http://localhost:8080/api/v1/forge');
    expect(forgeClient.post).toHaveBeenCalledWith('/sessions', config);
    expect(client.post).not.toHaveBeenCalledWith('/sessions', config);
  });

  it('derives a canonical forge client for session reads when the main base is legacy volundr', async () => {
    const client = makeClientWithBase('http://localhost:8080/api/v1/volundr');

    await buildVolundrHttpAdapter(client).getSessions();

    const forgeClient = getDerivedClient('http://localhost:8080/api/v1/forge');
    expect(forgeClient.get).toHaveBeenCalledWith('/sessions');
    expect(client.get).not.toHaveBeenCalledWith('/sessions');
  });

  it('stopSession calls POST /sessions/:id/stop', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).stopSession('s1');
    expect(client.post).toHaveBeenCalledWith('/sessions/s1/stop');
  });

  it('deleteSession calls DELETE /sessions/:id without cleanup', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).deleteSession('s1');
    expect(client.delete).toHaveBeenCalledWith('/sessions/s1');
  });

  it('deleteSession includes cleanup param when provided', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).deleteSession('s1', ['workspace']);
    expect(client.delete).toHaveBeenCalledWith('/sessions/s1?cleanup=workspace');
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

  it('savePreset calls POST /presets when no id', async () => {
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
    await buildVolundrHttpAdapter(client).savePreset(preset);
    expect(client.post).toHaveBeenCalledWith('/presets', preset);
  });

  it('savePreset calls PUT /presets/:id when id is present', async () => {
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
    await buildVolundrHttpAdapter(client).savePreset(preset);
    expect(client.put).toHaveBeenCalledWith('/presets/p1', preset);
  });

  it('getIdentity calls GET /identity', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).getIdentity();
    const sharedClient = getDerivedClient('http://localhost:8080/api/v1');
    expect(sharedClient.get).toHaveBeenCalledWith('/identity');
  });

  it('listArchivedSessions uses the archived status query instead of a synthetic sub-route', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).listArchivedSessions();
    expect(client.get).toHaveBeenCalledWith('/sessions?status=archived');
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
    await svc.getModels();
    await svc.getRepos();
    await svc.getTemplates();
    await svc.getTemplate('tpl-1');
    await svc.getPresets();
    await svc.getPreset('p1');
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
    await svc.saveTemplate({ name: 'tpl', description: '', config: {} } as Parameters<
      typeof svc.saveTemplate
    >[0]);
    await svc.updateAdminSettings({
      storage: { provider: 's3', bucket: 'b', region: 'us-east-1' },
    });
    await svc.updateUserFeaturePreferences([{ key: 'dark-mode', enabled: true }] as Parameters<
      typeof svc.updateUserFeaturePreferences
    >[0]);

    // DELETE methods
    await svc.deletePreset('p1');
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
