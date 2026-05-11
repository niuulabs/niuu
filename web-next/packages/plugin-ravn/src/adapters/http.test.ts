/**
 * HTTP adapter tests — adapted from web/src/modules/ravn/api/client.test.ts
 */
import { describe, it, expect, vi } from 'vitest';
import {
  buildRavnPersonaAdapter,
  buildRavnRavenAdapter,
  buildRavnSessionAdapter,
  buildRavnTriggerAdapter,
  buildRavnBudgetAdapter,
} from './http';
import type { IPersonaStore, PersonaCreateRequest } from '../ports';

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
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  };
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

  it('fetches messages for a session', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawMsg]);
    const result = await buildRavnSessionAdapter(client).getMessages(rawSession.id);
    expect(client.get).toHaveBeenCalledWith(`/sessions/${rawSession.id}/messages`);
    expect(result[0]!.sessionId).toBe(rawSession.id);
    expect(result[0]!.kind).toBe('asst');
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
