/**
 * HTTP adapter tests — adapted from web/src/modules/ravn/api/client.test.ts
 */
import { describe, it, expect, vi } from 'vitest';
import {
  buildRavnPersonaAdapter,
  buildRavnRavenAdapter,
  buildRavnResidentControlAdapter,
  buildRavnSessionAdapter,
  buildRavnTriggerAdapter,
  buildRavnBudgetAdapter,
  buildRavnWardenAdapter,
} from './http';
import type {
  IPersonaStore,
  IWardenStore,
  PersonaCreateRequest,
  WardenCreateRequest,
} from '../ports';

// ---------------------------------------------------------------------------
// Shared test fixtures
// ---------------------------------------------------------------------------

const rawSummary = {
  name: 'coder',
  role: 'build',
  letter: 'C',
  color: 'var(--color-accent-indigo)',
  summary: 'A coding agent',
  permission_mode: 'default',
  allowed_tools: ['read', 'write'],
  iteration_budget: 40,
  is_builtin: true,
  has_override: false,
  produces_event: 'code.changed',
  consumes_events: ['code.requested'],
};

const rawDetail = {
  ...rawSummary,
  description: 'Full coding agent description',
  system_prompt_template: 'You are a coder.',
  forbidden_tools: [],
  llm: { thinking_enabled: true, max_tokens: 8192 },
  produces: { event_type: 'code.changed', schema_def: { file: 'string' } },
  consumes: { events: [{ name: 'code.requested' }], schema_def: { topic: 'string' } },
  fan_in: { strategy: 'merge', params: {} },
  yaml_source: '[built-in]',
  override_source: '[user:user-1]',
};

function makeClient() {
  return {
    basePath: '/api/v1/ravn',
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  };
}

function mockSseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

// ---------------------------------------------------------------------------
// listPersonas
// ---------------------------------------------------------------------------

describe('listPersonas', () => {
  it('calls GET /personas?source=all by default', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawSummary]);
    await buildRavnPersonaAdapter(client).listPersonas();
    expect(client.get).toHaveBeenCalledWith('/personas?source=all');
  });

  it('transforms snake_case to camelCase', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawSummary]);
    const result = await buildRavnPersonaAdapter(client).listPersonas();
    expect(result[0]).toMatchObject({
      name: 'coder',
      role: 'build',
      permissionMode: 'default',
      allowedTools: ['read', 'write'],
      iterationBudget: 40,
      isBuiltin: true,
      hasOverride: false,
      producesEvent: 'code.changed',
      consumesEvents: ['code.requested'],
    });
  });

  it('fetches builtin personas with source=builtin', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([]);
    await buildRavnPersonaAdapter(client).listPersonas('builtin');
    expect(client.get).toHaveBeenCalledWith('/personas?source=builtin');
  });

  it('fetches custom personas with source=custom', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([]);
    await buildRavnPersonaAdapter(client).listPersonas('custom');
    expect(client.get).toHaveBeenCalledWith('/personas?source=custom');
  });

  it('propagates errors from the HTTP client', async () => {
    const client = makeClient();
    client.get.mockRejectedValue(new Error('network error'));
    await expect(buildRavnPersonaAdapter(client).listPersonas()).rejects.toThrow('network error');
  });
});

// ---------------------------------------------------------------------------
// getPersona
// ---------------------------------------------------------------------------

describe('getPersona', () => {
  it('calls GET /personas/:name', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(rawDetail);
    await buildRavnPersonaAdapter(client).getPersona('coder');
    expect(client.get).toHaveBeenCalledWith('/personas/coder');
  });

  it('URL-encodes persona names with special characters', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(rawDetail);
    await buildRavnPersonaAdapter(client).getPersona('my agent');
    expect(client.get).toHaveBeenCalledWith('/personas/my%20agent');
  });

  it('transforms detail response to camelCase', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(rawDetail);
    const result = await buildRavnPersonaAdapter(client).getPersona('coder');
    expect(result).toMatchObject({
      name: 'coder',
      systemPromptTemplate: 'You are a coder.',
      forbiddenTools: [],
      llm: { thinkingEnabled: true, maxTokens: 8192 },
      produces: { eventType: 'code.changed', schemaDef: { file: 'string' } },
      consumes: { events: [{ name: 'code.requested' }], schemaDef: { topic: 'string' } },
      fanIn: { strategy: 'merge', params: {} },
      yamlSource: '[built-in]',
      overrideSource: '[user:user-1]',
    });
  });
});

// ---------------------------------------------------------------------------
// getPersonaYaml
// ---------------------------------------------------------------------------

describe('getPersonaYaml', () => {
  it('calls GET /personas/:name/yaml', async () => {
    const client = makeClient();
    client.get.mockResolvedValue('name: coder\n');
    const result = await buildRavnPersonaAdapter(client).getPersonaYaml('coder');
    expect(client.get).toHaveBeenCalledWith('/personas/coder/yaml');
    expect(result).toBe('name: coder\n');
  });
});

// ---------------------------------------------------------------------------
// createPersona
// ---------------------------------------------------------------------------

describe('createPersona', () => {
  const req: PersonaCreateRequest = {
    name: 'new-agent',
    role: 'build',
    letter: 'N',
    color: 'var(--color-accent-cyan)',
    summary: 'New agent',
    description: 'New agent description',
    systemPromptTemplate: 'You are helpful.',
    allowedTools: ['read'],
    forbiddenTools: [],
    permissionMode: 'default',
    iterationBudget: 10,
    llmThinkingEnabled: false,
    llmMaxTokens: 8192,
    producesEventType: '',
    producesSchema: {},
    consumesEvents: [],
    consumesSchema: {},
  };

  it('calls POST /personas', async () => {
    const client = makeClient();
    client.post.mockResolvedValue(rawDetail);
    await buildRavnPersonaAdapter(client).createPersona(req);
    expect(client.post).toHaveBeenCalledWith('/personas', expect.any(Object));
  });

  it('converts camelCase request to snake_case body', async () => {
    const client = makeClient();
    client.post.mockResolvedValue(rawDetail);
    await buildRavnPersonaAdapter(client).createPersona(req);
    const body = client.post.mock.calls[0][1] as Record<string, unknown>;
    expect(body).toMatchObject({
      name: 'new-agent',
      role: 'build',
      system_prompt_template: 'You are helpful.',
      allowed_tools: ['read'],
      permission_mode: 'default',
      iteration_budget: 10,
      llm_thinking_enabled: false,
      llm_max_tokens: 8192,
      produces_schema: {},
      consumes_events: [],
      consumes_schema: {},
    });
  });

  it('returns a camelCase PersonaDetail', async () => {
    const client = makeClient();
    client.post.mockResolvedValue(rawDetail);
    const result = await buildRavnPersonaAdapter(client).createPersona(req);
    expect(result.systemPromptTemplate).toBe('You are a coder.');
    expect(result.llm.thinkingEnabled).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// updatePersona
// ---------------------------------------------------------------------------

describe('updatePersona', () => {
  const req: PersonaCreateRequest = {
    name: 'coder',
    role: 'build',
    letter: 'C',
    color: 'var(--color-accent-indigo)',
    summary: 'Updated',
    description: 'Updated description',
    systemPromptTemplate: 'Updated.',
    allowedTools: [],
    forbiddenTools: [],
    permissionMode: 'default',
    iterationBudget: 0,
    llmThinkingEnabled: false,
    llmMaxTokens: 8192,
    producesEventType: '',
    producesSchema: {},
    consumesEvents: [],
    consumesSchema: {},
  };

  it('calls PUT /personas/:name', async () => {
    const client = makeClient();
    client.put.mockResolvedValue(rawDetail);
    await buildRavnPersonaAdapter(client).updatePersona('coder', req);
    expect(client.put).toHaveBeenCalledWith('/personas/coder', expect.any(Object));
  });
});

// ---------------------------------------------------------------------------
// deletePersona
// ---------------------------------------------------------------------------

describe('deletePersona', () => {
  it('calls DELETE /personas/:name', async () => {
    const client = makeClient();
    client.delete.mockResolvedValue(undefined);
    await buildRavnPersonaAdapter(client).deletePersona('my-agent');
    expect(client.delete).toHaveBeenCalledWith('/personas/my-agent');
  });
});

// ---------------------------------------------------------------------------
// forkPersona
// ---------------------------------------------------------------------------

describe('forkPersona', () => {
  it('calls POST /personas/:name/fork', async () => {
    const client = makeClient();
    client.post.mockResolvedValue(rawDetail);
    await buildRavnPersonaAdapter(client).forkPersona('coder', { newName: 'my-fork' });
    expect(client.post).toHaveBeenCalledWith('/personas/coder/fork', expect.any(Object));
  });

  it('sends new_name in request body', async () => {
    const client = makeClient();
    client.post.mockResolvedValue(rawDetail);
    await buildRavnPersonaAdapter(client).forkPersona('coder', { newName: 'my-fork' });
    const body = client.post.mock.calls[0][1] as Record<string, unknown>;
    expect(body.new_name).toBe('my-fork');
  });

  it('returns a PersonaDetail for the forked persona', async () => {
    const client = makeClient();
    client.post.mockResolvedValue(rawDetail);
    const result = await buildRavnPersonaAdapter(client).forkPersona('coder', {
      newName: 'my-fork',
    });
    expect(result.name).toBe('coder');
  });
});

// ---------------------------------------------------------------------------
// Interface compliance
// ---------------------------------------------------------------------------

describe('buildRavnPersonaAdapter', () => {
  it('satisfies the IPersonaStore interface', () => {
    const client = makeClient();
    const store: IPersonaStore = buildRavnPersonaAdapter(client);
    expect(typeof store.listPersonas).toBe('function');
    expect(typeof store.getPersona).toBe('function');
    expect(typeof store.getPersonaYaml).toBe('function');
    expect(typeof store.createPersona).toBe('function');
    expect(typeof store.updatePersona).toBe('function');
    expect(typeof store.deletePersona).toBe('function');
    expect(typeof store.forkPersona).toBe('function');
  });
});

// ---------------------------------------------------------------------------
// buildRavnRavenAdapter
// ---------------------------------------------------------------------------

const rawRavn = {
  id: '11111111-1111-4111-8111-111111111111',
  persona_name: 'coder',
  status: 'active',
  model: 'balanced',
  created_at: '2026-04-01T12:00:00Z',
  updated_at: '2026-04-02T09:30:00Z',
  location: 'midgard',
  deployment: 'standalone',
};

describe('buildRavnRavenAdapter', () => {
  it('lists ravens from GET /ravens and camel-cases the response', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawRavn]);
    const result = await buildRavnRavenAdapter(client).listRavens();
    expect(client.get).toHaveBeenCalledWith('/ravens');
    expect(result).toEqual([
      {
        id: rawRavn.id,
        personaName: 'coder',
        status: 'active',
        model: 'balanced',
        createdAt: rawRavn.created_at,
        updatedAt: rawRavn.updated_at,
        location: 'midgard',
        deployment: 'standalone',
      },
    ]);
  });

  it('fetches a single raven by id', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(rawRavn);
    const result = await buildRavnRavenAdapter(client).getRaven(rawRavn.id);
    expect(client.get).toHaveBeenCalledWith(`/ravens/${rawRavn.id}`);
    expect(result.personaName).toBe('coder');
  });

  it('maps resident wire fields to camelCase', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({
      ...rawRavn,
      resident_name: 'huginn',
      peer_id: 'peer-huginn-01',
      kind: 'resident',
      chat_endpoint: 'wss://skuld.example/s/abc/session',
      session_id: '33333333-3333-4333-8333-333333333333',
    });
    const result = await buildRavnRavenAdapter(client).getRaven(rawRavn.id);
    expect(result.residentName).toBe('huginn');
    expect(result.peerId).toBe('peer-huginn-01');
    expect(result.kind).toBe('resident');
    expect(result.chatEndpoint).toBe('wss://skuld.example/s/abc/session');
    expect(result.sessionId).toBe('33333333-3333-4333-8333-333333333333');
  });

  it('normalizes the complete managed resident contract from mixed backend records', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({
      ...rawRavn,
      backend: 'openshell',
      engine: 'hermes',
      profile_id: 'nemohermes-openshell',
      desired_state: 'running',
      observed_state: 'active',
      backend_ref: 'sandbox/hermes-1',
      capabilities: ['chat', 'logs', 'metrics'],
      conditions: [
        {
          type: 'Ready',
          status: 'true',
          reason: null,
          message: null,
          last_transition_at: '2026-04-02T09:31:00Z',
        },
        {
          type: 'ChatReady',
          status: 'unknown',
          lastTransitionAt: '2026-04-02T09:32:00Z',
        },
        { type: 'Configured', status: 'true' },
      ],
      endpoints: [{ kind: 'metrics', protocol: 'http', url: 'https://metrics.example/resident' }],
      managed: true,
      instance_id: 'target-1',
      instance_name: 'Compute target',
      instance_slug: 'compute',
      message_count: 7,
      tokens_used: 42,
      cost: '0.25',
    });

    const result = await buildRavnRavenAdapter(client).getRaven(rawRavn.id);

    expect(result).toMatchObject({
      backend: 'openshell',
      engine: 'hermes',
      profileId: 'nemohermes-openshell',
      desiredState: 'running',
      observedState: 'active',
      backendRef: 'sandbox/hermes-1',
      capabilities: ['chat', 'logs', 'metrics'],
      managed: true,
      instanceId: 'target-1',
      instanceName: 'Compute target',
      instanceSlug: 'compute',
      messageCount: 7,
      tokenCount: 42,
      costUsd: 0.25,
    });
    expect(result.conditions).toEqual([
      {
        type: 'Ready',
        status: 'true',
        reason: '',
        message: '',
        lastTransitionAt: '2026-04-02T09:31:00Z',
      },
      {
        type: 'ChatReady',
        status: 'unknown',
        reason: '',
        message: '',
        lastTransitionAt: '2026-04-02T09:32:00Z',
      },
      {
        type: 'Configured',
        status: 'true',
        reason: '',
        message: '',
        lastTransitionAt: '',
      },
    ]);
    expect(result.endpoints).toEqual([
      { kind: 'metrics', protocol: 'http', url: 'https://metrics.example/resident' },
    ]);
  });

  it('maps a null chat_endpoint through unchanged', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({ ...rawRavn, kind: 'resident', chat_endpoint: null });
    const result = await buildRavnRavenAdapter(client).getRaven(rawRavn.id);
    expect(result.chatEndpoint).toBeNull();
  });

  it('leaves resident fields undefined when absent from the wire record', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(rawRavn);
    const result = await buildRavnRavenAdapter(client).getRaven(rawRavn.id);
    expect(result.residentName).toBeUndefined();
    expect(result.peerId).toBeUndefined();
    expect(result.kind).toBeUndefined();
    expect(result.chatEndpoint).toBeUndefined();
    expect(result.sessionId).toBeUndefined();
  });
});

describe('buildRavnResidentControlAdapter', () => {
  it('maps target-compatible deployment profiles', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([
      {
        id: 'nemohermes-openshell',
        displayName: 'NemoHermes',
        description: 'NVIDIA Hermes resident',
        backend: 'openshell',
        engine: 'hermes',
        capabilities: ['chat', 'session.create'],
        defaultModel: 'qwen3.5',
        allowedModels: ['qwen3.5'],
        labels: ['nvidia'],
        instance_id: 'target-1',
        instance_name: 'Compute target',
        instance_slug: 'compute',
      },
    ]);

    const profiles = await buildRavnResidentControlAdapter(client).listProfiles();

    expect(client.get).toHaveBeenCalledWith('/deployment-profiles');
    expect(profiles[0]).toMatchObject({
      id: 'nemohermes-openshell',
      instanceId: 'target-1',
      engine: 'hermes',
      allowedModels: ['qwen3.5'],
    });
  });

  it('deploys with opaque target context and maps the returned resident', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({
      ...rawRavn,
      managed: true,
      instance_id: 'target-1',
      backend: 'openshell',
      engine: 'hermes',
      profile_id: 'nemohermes-openshell',
      desired_state: 'running',
      observed_state: 'deploying',
      capabilities: ['chat', 'session.create'],
      conditions: [],
      endpoints: [],
    });
    const control = buildRavnResidentControlAdapter(client);

    const ravn = await control.deploy({
      name: 'Sol',
      profileId: 'nemohermes-openshell',
      instanceId: 'target-1',
      personaName: 'product-steward',
      model: 'qwen3.5',
    });

    expect(client.post).toHaveBeenCalledWith('/ravens', {
      name: 'Sol',
      profile_id: 'nemohermes-openshell',
      instance_id: 'target-1',
      persona_name: 'product-steward',
      model: 'qwen3.5',
    });
    expect(ravn).toMatchObject({
      managed: true,
      instanceId: 'target-1',
      observedState: 'deploying',
    });
  });

  it('routes lifecycle and native session commands through the owner', async () => {
    const client = makeClient();
    const control = buildRavnResidentControlAdapter(client);
    const ravn = {
      ...rawRavn,
      personaName: rawRavn.persona_name,
      createdAt: rawRavn.created_at,
      status: 'active' as const,
      instanceId: 'target/one',
    };
    client.post.mockResolvedValueOnce({ ...rawRavn }).mockResolvedValueOnce({
      id: '22222222-2222-4222-8222-222222222222',
      ravn_id: rawRavn.id,
      persona_name: 'coder',
      status: 'running',
      model: 'qwen3.5',
      created_at: rawRavn.created_at,
    });

    await control.applyLifecycle(ravn, 'restart');
    await control.createSession(ravn, { title: 'Second thread', model: 'qwen3.5' });
    await control.deleteSession(ravn, 'session/two');

    expect(client.post).toHaveBeenNthCalledWith(
      1,
      `/ravens/${rawRavn.id}/restart?instance_id=target%2Fone`,
      {},
    );
    expect(client.post).toHaveBeenNthCalledWith(
      2,
      `/ravens/${rawRavn.id}/sessions?instance_id=target%2Fone`,
      { title: 'Second thread', model: 'qwen3.5' },
    );
    expect(client.delete).toHaveBeenCalledWith(
      `/ravens/${rawRavn.id}/sessions/session%2Ftwo?instance_id=target%2Fone`,
    );
  });

  it('normalizes partial backend logs without inventing values', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({
      entries: [
        { timestamp_ms: 12, level: 'info', source: 'engine', message: 'ready' },
        { timestampMs: 13, target: 'api', fields: { port: 8000 } },
        {},
      ],
      bufferTotal: 3,
    });
    const ravn = {
      ...rawRavn,
      personaName: rawRavn.persona_name,
      createdAt: rawRavn.created_at,
      status: 'active' as const,
      instanceId: 'target-1',
    };

    const result = await buildRavnResidentControlAdapter(client).getLogs(ravn);

    expect(client.get).toHaveBeenCalledWith(`/ravens/${rawRavn.id}/logs?instance_id=target-1`);
    expect(result).toEqual({
      entries: [
        {
          timestampMs: 12,
          level: 'info',
          source: 'engine',
          target: '',
          message: 'ready',
          fields: {},
        },
        {
          timestampMs: 13,
          level: '',
          source: '',
          target: 'api',
          message: '',
          fields: { port: 8000 },
        },
        { timestampMs: 0, level: '', source: '', target: '', message: '', fields: {} },
      ],
      bufferTotal: 3,
    });
  });
});

// ---------------------------------------------------------------------------
// buildRavnSessionAdapter
// ---------------------------------------------------------------------------

const rawSession = {
  id: '22222222-2222-4222-8222-222222222222',
  ravn_id: '11111111-1111-4111-8111-111111111111',
  persona_name: 'coder',
  status: 'running',
  model: 'balanced',
  created_at: '2026-04-01T12:00:00Z',
};

const rawMsg = {
  id: '33333333-3333-4333-8333-333333333333',
  session_id: rawSession.id,
  kind: 'asst',
  content: 'hello',
  ts: '2026-04-01T12:00:05Z',
  tool_name: undefined,
};

describe('buildRavnSessionAdapter', () => {
  it('lists sessions and camel-cases', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawSession]);
    const result = await buildRavnSessionAdapter(client).listSessions();
    expect(client.get).toHaveBeenCalledWith('/sessions');
    expect(result[0]!.ravnId).toBe(rawSession.ravn_id);
  });

  it('fetches a session by id', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(rawSession);
    await buildRavnSessionAdapter(client).getSession(rawSession.id);
    expect(client.get).toHaveBeenCalledWith(`/sessions/${rawSession.id}`);
  });

  it('maps resident usage and title fields', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({
      ...rawSession,
      title: 'resident coder',
      message_count: 3,
      tokens_used: 54729,
      cost: '0.094503',
    });

    const result = await buildRavnSessionAdapter(client).getSession(rawSession.id);

    expect(result).toMatchObject({
      title: 'resident coder',
      messageCount: 3,
      tokenCount: 54729,
      costUsd: 0.094503,
    });
  });

  it('fetches messages for a session', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawMsg]);
    const result = await buildRavnSessionAdapter(client).getMessages(rawSession.id);
    expect(client.get).toHaveBeenCalledWith(`/sessions/${rawSession.id}/messages`);
    expect(result[0]!.sessionId).toBe(rawSession.id);
    expect(result[0]!.kind).toBe('asst');
  });

  it('maps chat_endpoint to chatEndpoint when present', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({
      ...rawSession,
      chat_endpoint: 'wss://skuld.example/s/abc/session',
    });
    const result = await buildRavnSessionAdapter(client).getSession(rawSession.id);
    expect(result.chatEndpoint).toBe('wss://skuld.example/s/abc/session');
  });

  it('carries opaque owning instance context into resident chat URLs', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({
      ...rawSession,
      chat_endpoint: '/s/resident/sessions/thread/session',
      instance_id: 'target/one',
    });
    const result = await buildRavnSessionAdapter(client).getSession(rawSession.id);
    expect(result.chatEndpoint).toBe(
      '/s/resident/sessions/thread/session?instance_id=target%2Fone',
    );
    expect(result.instanceId).toBe('target/one');
  });

  it('preserves existing query parameters and does not duplicate instance context', async () => {
    const client = makeClient();
    const adapter = buildRavnSessionAdapter(client);
    client.get
      .mockResolvedValueOnce({
        ...rawSession,
        chat_endpoint: '/s/resident/session?mode=chat',
        instance_id: 'target one',
      })
      .mockResolvedValueOnce({
        ...rawSession,
        chat_endpoint: '/s/resident/session?instance_id=target-one',
        instance_id: 'target-one',
      });

    const withQuery = await adapter.getSession(rawSession.id);
    const alreadyScoped = await adapter.getSession(rawSession.id);

    expect(withQuery.chatEndpoint).toBe('/s/resident/session?mode=chat&instance_id=target%20one');
    expect(alreadyScoped.chatEndpoint).toBe('/s/resident/session?instance_id=target-one');
  });

  it('maps a null chat_endpoint through unchanged', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({ ...rawSession, chat_endpoint: null });
    const result = await buildRavnSessionAdapter(client).getSession(rawSession.id);
    expect(result.chatEndpoint).toBeNull();
  });

  it('leaves chatEndpoint undefined when absent from the wire record', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(rawSession);
    const result = await buildRavnSessionAdapter(client).getSession(rawSession.id);
    expect(result.chatEndpoint).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// buildRavnTriggerAdapter
// ---------------------------------------------------------------------------

const rawTrigger = {
  id: '44444444-4444-4444-8444-444444444444',
  kind: 'cron',
  persona_name: 'coder',
  spec: '0 * * * *',
  enabled: true,
  created_at: '2026-04-01T12:00:00Z',
};

describe('buildRavnTriggerAdapter', () => {
  it('lists triggers', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawTrigger]);
    const result = await buildRavnTriggerAdapter(client).listTriggers();
    expect(result[0]!.personaName).toBe('coder');
  });

  it('creates a trigger with snake_case body', async () => {
    const client = makeClient();
    client.post.mockResolvedValue(rawTrigger);
    await buildRavnTriggerAdapter(client).createTrigger({
      kind: 'cron',
      personaName: 'coder',
      spec: '0 * * * *',
      enabled: true,
    });
    expect(client.post).toHaveBeenCalledWith('/triggers', {
      kind: 'cron',
      persona_name: 'coder',
      spec: '0 * * * *',
      enabled: true,
    });
  });

  it('deletes a trigger by id', async () => {
    const client = makeClient();
    client.delete.mockResolvedValue(undefined);
    await buildRavnTriggerAdapter(client).deleteTrigger(rawTrigger.id);
    expect(client.delete).toHaveBeenCalledWith(`/triggers/${rawTrigger.id}`);
  });
});

// ---------------------------------------------------------------------------
// buildRavnBudgetAdapter
// ---------------------------------------------------------------------------

const rawBudget = { spent_usd: 42.5, cap_usd: 100, warn_at: 0.8 };

describe('buildRavnBudgetAdapter', () => {
  it('fetches per-ravn budget', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(rawBudget);
    const result = await buildRavnBudgetAdapter(client).getBudget('ravn-1');
    expect(client.get).toHaveBeenCalledWith('/budget/ravn-1');
    expect(result).toEqual({ spentUsd: 42.5, capUsd: 100, warnAt: 0.8 });
  });

  it('fetches the fleet budget', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(rawBudget);
    await buildRavnBudgetAdapter(client).getFleetBudget();
    expect(client.get).toHaveBeenCalledWith('/budget/fleet');
  });
});

// ---------------------------------------------------------------------------
// buildRavnWardenAdapter
// ---------------------------------------------------------------------------

const rawWarden = {
  id: 'ravn-fjolnir',
  name: 'Ravn Fjolnir',
  persona: 'research-and-distill',
  profile: 'infra-synthesis',
  model: 'claude-sonnet-4-6',
  deployment: 'launchd',
  deployment_kwargs: {
    namespace: 'ravn-dev',
    auto_commit: true,
  },
  mimir: {
    mount_names: ['local', 'shared'],
    write_mount: 'local',
    read_mount_names: ['local', 'shared'],
    write_mount_names: ['local'],
    category_scope: ['infra'],
  },
  features: {
    wakefulness_enabled: true,
    dream_cycle_enabled: true,
    thread_queue_enabled: true,
    thread_enricher_enabled: true,
    recap_enabled: true,
    source_trigger_enabled: true,
    staleness_trigger_enabled: false,
  },
  schedules: {
    dream_cycle_cron_expression: '0 3 * * *',
    dream_cycle_poll_interval_seconds: 60,
    source_trigger_poll_interval_seconds: 90,
    staleness_trigger_schedule_hours: 8,
  },
  console: {
    enabled: true,
    host: '0.0.0.0',
    port: 8412,
    public_host: 'warden.example.test',
    auth_mode: 'noop',
  },
  autostart: true,
  created_at: '2026-04-20T11:00:00Z',
  created_by: 'operator',
  runtime: {
    state: 'idle',
    pages_touched: 12,
    last_started_at: '2026-04-20T11:05:00Z',
    last_dream: null,
  },
  supervisor: {
    installed: true,
    service_label: 'dev.niuu.ravn.warden.ravn-fjolnir',
    service_file: '/tmp/ravn-fjolnir.plist',
    config_file: '/tmp/ravn-fjolnir.yaml',
    start_command: 'ravn daemon --config /tmp/ravn-fjolnir.yaml',
    stdout_log: '/tmp/ravn-fjolnir.log',
    stderr_log: '/tmp/ravn-fjolnir.err.log',
    last_install_at: '2026-04-20T11:01:00Z',
    observation: {
      status: 'running',
      detail: 'launchd reports the agent as running',
      source: 'launchctl',
      checked_at: '2026-04-20T11:06:00Z',
      fields: [{ label: 'pid', value: '1234' }],
    },
  },
};

describe('buildRavnWardenAdapter', () => {
  it('lists wardens from GET /wardens and camel-cases the response', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawWarden]);
    const result = await buildRavnWardenAdapter(client).listWardens();
    expect(client.get).toHaveBeenCalledWith('/wardens');
    expect(result).toEqual([
      expect.objectContaining({
        id: 'ravn-fjolnir',
        model: 'claude-sonnet-4-6',
        deploymentKwargs: {
          namespace: 'ravn-dev',
          auto_commit: true,
        },
        mountNames: ['local', 'shared'],
        writeMount: 'local',
        readMountNames: ['local', 'shared'],
        writeMountNames: ['local'],
        categoryScope: ['infra'],
        autostart: true,
        createdAt: '2026-04-20T11:00:00Z',
        createdBy: 'operator',
        schedules: expect.objectContaining({
          dreamCycleCronExpression: '0 3 * * *',
          sourceTriggerPollIntervalSeconds: 90,
          stalenessTriggerScheduleHours: 8,
        }),
        console: expect.objectContaining({
          enabled: true,
          port: 8412,
          publicHost: 'warden.example.test',
        }),
        runtime: expect.objectContaining({
          state: 'idle',
          pagesTouched: 12,
          lastStartedAt: '2026-04-20T11:05:00Z',
        }),
        supervisor: expect.objectContaining({
          installed: true,
          serviceLabel: 'dev.niuu.ravn.warden.ravn-fjolnir',
          serviceFile: '/tmp/ravn-fjolnir.plist',
          observation: expect.objectContaining({
            status: 'running',
            source: 'launchctl',
          }),
        }),
        features: expect.objectContaining({
          wakefulnessEnabled: true,
          stalenessTriggerEnabled: false,
        }),
      }),
    ]);
  });

  it('fetches a single warden by id', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(rawWarden);
    const result = await buildRavnWardenAdapter(client).getWarden(rawWarden.id);
    expect(client.get).toHaveBeenCalledWith(`/wardens/${rawWarden.id}`);
    expect(result.name).toBe('Ravn Fjolnir');
  });

  it('converts camelCase create requests to snake_case wire format', async () => {
    const client = makeClient();
    client.post.mockResolvedValue(rawWarden);
    const req: WardenCreateRequest = {
      name: 'Ravn Fjolnir',
      persona: 'research-and-distill',
      profile: 'infra-synthesis',
      model: 'gpt-5.5',
      deployment: 'launchd',
      deploymentKwargs: {
        namespace: 'ravn-dev',
        auto_commit: true,
      },
      mountNames: ['local', 'shared'],
      writeMount: 'local',
      readMountNames: ['local', 'shared'],
      writeMountNames: ['local'],
      categoryScope: ['infra'],
      schedules: {
        dreamCycleCronExpression: '15 2 * * *',
        dreamCyclePollIntervalSeconds: 30,
        sourceTriggerPollIntervalSeconds: 75,
        stalenessTriggerScheduleHours: 12,
      },
      console: {
        enabled: true,
        host: '127.0.0.1',
        port: 8500,
        publicHost: 'warden.example.test',
        authMode: 'noop',
      },
      autostart: true,
      createdBy: 'operator',
      features: {
        wakefulnessEnabled: true,
        dreamCycleEnabled: true,
        threadQueueEnabled: false,
      },
    };
    await buildRavnWardenAdapter(client).createWarden(req);
    expect(client.post).toHaveBeenCalledWith('/wardens', {
      name: 'Ravn Fjolnir',
      persona: 'research-and-distill',
      profile: 'infra-synthesis',
      model: 'gpt-5.5',
      deployment: 'launchd',
      deployment_kwargs: {
        namespace: 'ravn-dev',
        auto_commit: true,
      },
      mount_names: ['local', 'shared'],
      write_mount: 'local',
      read_mount_names: ['local', 'shared'],
      write_mount_names: ['local'],
      category_scope: ['infra'],
      features: {
        wakefulness_enabled: true,
        dream_cycle_enabled: true,
        thread_queue_enabled: false,
        thread_enricher_enabled: undefined,
        recap_enabled: undefined,
        source_trigger_enabled: undefined,
        staleness_trigger_enabled: undefined,
      },
      schedules: {
        dream_cycle_cron_expression: '15 2 * * *',
        dream_cycle_poll_interval_seconds: 30,
        source_trigger_poll_interval_seconds: 75,
        staleness_trigger_schedule_hours: 12,
      },
      console: {
        enabled: true,
        host: '127.0.0.1',
        port: 8500,
        public_host: 'warden.example.test',
        auth_mode: 'noop',
      },
      autostart: true,
      created_by: 'operator',
    });
  });

  it('satisfies the IWardenStore interface', () => {
    const client = makeClient();
    const store: IWardenStore = buildRavnWardenAdapter(client);
    expect(typeof store.listWardens).toBe('function');
    expect(typeof store.getWarden).toBe('function');
    expect(typeof store.createWarden).toBe('function');
    expect(typeof store.subscribeWarden).toBe('function');
    expect(typeof store.observeWarden).toBe('function');
    expect(typeof store.installWarden).toBe('function');
    expect(typeof store.startWarden).toBe('function');
    expect(typeof store.stopWarden).toBe('function');
    expect(typeof store.uninstallWarden).toBe('function');
    expect(typeof store.getWardenLogs).toBe('function');
    expect(typeof store.getWardenActivity).toBe('function');
  });

  it('subscribes to per-warden SSE updates', async () => {
    const originalFetch = global.fetch;
    global.fetch = vi.fn(async () =>
      mockSseResponse([`event: warden.observed\ndata: ${JSON.stringify(rawWarden)}\n\n`]),
    );
    const listener = vi.fn();

    const unsubscribe = buildRavnWardenAdapter(makeClient()).subscribeWarden(
      rawWarden.id,
      listener,
    );
    await new Promise((resolve) => setTimeout(resolve, 10));
    unsubscribe();
    global.fetch = originalFetch;

    expect(listener).toHaveBeenCalledWith(
      expect.objectContaining({
        id: rawWarden.id,
        supervisor: expect.objectContaining({
          observation: expect.objectContaining({
            status: 'running',
          }),
        }),
      }),
    );
  });

  it('posts install actions to the warden install endpoint', async () => {
    const client = makeClient();
    client.post.mockResolvedValue(rawWarden);
    const result = await buildRavnWardenAdapter(client).installWarden(rawWarden.id);
    expect(client.post).toHaveBeenCalledWith(`/wardens/${rawWarden.id}/install`, {});
    expect(result.supervisor?.installed).toBe(true);
  });

  it('posts observe actions to the warden observe endpoint', async () => {
    const client = makeClient();
    client.post.mockResolvedValue(rawWarden);
    const result = await buildRavnWardenAdapter(client).observeWarden(rawWarden.id);
    expect(client.post).toHaveBeenCalledWith(`/wardens/${rawWarden.id}/observe`, {});
    expect(result.supervisor?.observation?.status).toBe('running');
  });

  it('posts start actions to the warden start endpoint', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({
      ...rawWarden,
      runtime: {
        ...rawWarden.runtime,
        state: 'active',
      },
    });
    const result = await buildRavnWardenAdapter(client).startWarden(rawWarden.id);
    expect(client.post).toHaveBeenCalledWith(`/wardens/${rawWarden.id}/start`, {});
    expect(result.runtime?.state).toBe('active');
  });

  it('posts stop actions to the warden stop endpoint', async () => {
    const client = makeClient();
    client.post.mockResolvedValue(rawWarden);
    const result = await buildRavnWardenAdapter(client).stopWarden(rawWarden.id);
    expect(client.post).toHaveBeenCalledWith(`/wardens/${rawWarden.id}/stop`, {});
    expect(result.runtime?.state).toBe('idle');
  });

  it('posts uninstall actions to the warden uninstall endpoint', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({
      ...rawWarden,
      runtime: {
        ...rawWarden.runtime,
        state: 'offline',
      },
      supervisor: {
        ...rawWarden.supervisor,
        installed: false,
      },
    });
    const result = await buildRavnWardenAdapter(client).uninstallWarden(rawWarden.id);
    expect(client.post).toHaveBeenCalledWith(`/wardens/${rawWarden.id}/uninstall`, {});
    expect(result.supervisor?.installed).toBe(false);
    expect(result.runtime?.state).toBe('offline');
  });

  it('fetches stdout log entries', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([
      {
        id: 'line-1',
        source: 'stdout',
        line_number: 1,
        raw: 'raw',
        timestamp: '2026-04-20T11:00:00Z',
        level: 'INFO',
        logger: 'ravn.daemon',
        message: 'ravn daemon started.',
      },
    ]);
    const result = await buildRavnWardenAdapter(client).getWardenLogs(rawWarden.id, {
      stream: 'stdout',
      limit: 50,
    });
    expect(client.get).toHaveBeenCalledWith(`/wardens/${rawWarden.id}/logs?stream=stdout&limit=50`);
    expect(result[0]).toMatchObject({
      source: 'stdout',
      lineNumber: 1,
      message: 'ravn daemon started.',
    });
  });

  it('fetches recent warden activity', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([
      {
        id: 'activity-1',
        source: 'stderr',
        line_number: 1,
        raw: 'raw',
        timestamp: '2026-04-20T11:00:00Z',
        level: 'WARN',
        logger: 'ravn.dream',
        message: 'warden is installed but idle',
      },
    ]);
    const result = await buildRavnWardenAdapter(client).getWardenActivity(rawWarden.id, 20);
    expect(client.get).toHaveBeenCalledWith(`/wardens/${rawWarden.id}/activity?limit=20`);
    expect(result[0]).toMatchObject({
      source: 'stderr',
      message: 'warden is installed but idle',
    });
  });

  it('maps sparse and alternate warden response branches', async () => {
    const client = makeClient();
    const sparse = {
      ...rawWarden,
      model: '',
      deployment_kwargs: undefined,
      mimir: {
        ...rawWarden.mimir,
        write_mount: '',
        read_mount_names: undefined,
        write_mount_names: undefined,
      },
      schedules: undefined,
      console: undefined,
      runtime: undefined,
      supervisor: undefined,
      operator: {
        rune: 'R',
        bio: 'Reviews changes',
        expertise: ['review'],
        tools: ['git'],
        role: 'review',
      },
    };
    const withDream = {
      ...rawWarden,
      mimir: { ...rawWarden.mimir, write_mount_names: undefined },
      runtime: {
        ...rawWarden.runtime,
        last_dream: {
          id: 'dream-1',
          timestamp: '2026-04-20T12:00:00Z',
          ravn: 'ravn-fjolnir',
          mounts: ['local'],
          pages_updated: 2,
          entities_created: 1,
          lint_fixes: 3,
          duration_ms: 4000,
        },
      },
      supervisor: {
        ...rawWarden.supervisor,
        observation: { ...rawWarden.supervisor.observation, fields: undefined },
      },
    };
    client.get.mockResolvedValue([sparse, withDream]);

    const [mappedSparse, mappedDream] = await buildRavnWardenAdapter(client).listWardens();
    expect(mappedSparse).toMatchObject({
      model: 'claude-sonnet-4-6',
      deploymentKwargs: {},
      readMountNames: ['local', 'shared'],
      writeMountNames: [],
      runtime: undefined,
      supervisor: undefined,
      operator: { role: 'review' },
    });
    expect(mappedSparse?.schedules.dreamCyclePollIntervalSeconds).toBe(60);
    expect(mappedSparse?.console.port).toBe(8400);
    expect(mappedDream?.writeMountNames).toEqual(['local']);
    expect(mappedDream?.runtime?.lastDream).toMatchObject({ id: 'dream-1', durationMs: 4000 });
    expect(mappedDream?.supervisor?.observation?.fields).toBeUndefined();
  });

  it('uses empty request defaults for a minimal warden create', async () => {
    const client = makeClient();
    client.post.mockResolvedValue(rawWarden);
    await buildRavnWardenAdapter(client).createWarden({ name: 'Minimal' });
    expect(client.post).toHaveBeenCalledWith(
      '/wardens',
      expect.objectContaining({
        deployment_kwargs: {},
        mount_names: [],
        write_mount: '',
        read_mount_names: [],
        write_mount_names: [],
        category_scope: [],
        features: undefined,
        schedules: undefined,
        console: undefined,
      }),
    );
  });

  it('drops malformed SSE frames when no client base path is configured', async () => {
    const originalFetch = global.fetch;
    global.fetch = vi.fn(async () => mockSseResponse(['data: not-json\n\n']));
    const listener = vi.fn();
    const client = { ...makeClient(), basePath: undefined };

    const unsubscribe = buildRavnWardenAdapter(client).subscribeWarden(rawWarden.id, listener);
    await new Promise((resolve) => setTimeout(resolve, 10));
    unsubscribe();
    global.fetch = originalFetch;

    expect(listener).not.toHaveBeenCalled();
  });

  it('builds optional log and activity query strings', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([]);
    const adapter = buildRavnWardenAdapter(client);
    await adapter.getWardenLogs(rawWarden.id);
    await adapter.getWardenLogs(rawWarden.id, { limit: 10 });
    await adapter.getWardenActivity(rawWarden.id);
    expect(client.get).toHaveBeenNthCalledWith(1, `/wardens/${rawWarden.id}/logs`);
    expect(client.get).toHaveBeenNthCalledWith(2, `/wardens/${rawWarden.id}/logs?limit=10`);
    expect(client.get).toHaveBeenNthCalledWith(3, `/wardens/${rawWarden.id}/activity`);
  });
});
