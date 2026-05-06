import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  listPersonas,
  getPersona,
  getPersonaYaml,
  createPersona,
  updatePersona,
  deletePersona,
  forkPersona,
} from './client';
import type { PersonaCreateRequest } from './types';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

beforeEach(() => {
  mockFetch.mockReset();
});

const rawSummary = {
  name: 'coding-agent',
  permission_mode: 'workspace-write',
  allowed_tools: ['file', 'git'],
  iteration_budget: 40,
  is_builtin: true,
  has_override: false,
  produces_event: 'code.done',
  consumes_events: ['task.assigned'],
};

const rawDetail = {
  ...rawSummary,
  system_prompt_template: 'You are a coder.',
  forbidden_tools: ['cascade'],
  llm: { primary_alias: 'balanced', thinking_enabled: true, max_tokens: 0 },
  produces: { event_type: 'code.done', schema_def: {} },
  consumes: { event_types: ['task.assigned'], injects: ['repo'] },
  fan_in: { strategy: 'merge', contributes_to: '' },
  yaml_source: '[built-in]',
};

function mockOk(data: unknown, status = 200) {
  mockFetch.mockResolvedValue({
    status,
    ok: true,
    json: async () => data,
  });
}

describe('listPersonas', () => {
  it('fetches all personas with source=all by default', async () => {
    mockOk([rawSummary]);
    const result = await listPersonas();
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/personas?source=all'),
      expect.any(Object)
    );
    expect(result).toHaveLength(1);
  });

  it('transforms snake_case to camelCase', async () => {
    mockOk([rawSummary]);
    const result = await listPersonas();
    expect(result[0]).toMatchObject({
      name: 'coding-agent',
      permissionMode: 'workspace-write',
      allowedTools: ['file', 'git'],
      iterationBudget: 40,
      isBuiltin: true,
      hasOverride: false,
      producesEvent: 'code.done',
      consumesEvents: ['task.assigned'],
    });
  });

  it('fetches builtin personas with source=builtin', async () => {
    mockOk([]);
    await listPersonas('builtin');
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('source=builtin'),
      expect.any(Object)
    );
  });

  it('fetches custom personas with source=custom', async () => {
    mockOk([]);
    await listPersonas('custom');
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('source=custom'),
      expect.any(Object)
    );
  });
});

describe('getPersona', () => {
  it('fetches persona by name', async () => {
    mockOk(rawDetail);
    await getPersona('coding-agent');
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/personas/coding-agent'),
      expect.any(Object)
    );
  });

  it('transforms detail response to camelCase', async () => {
    mockOk(rawDetail);
    const result = await getPersona('coding-agent');
    expect(result).toMatchObject({
      name: 'coding-agent',
      systemPromptTemplate: 'You are a coder.',
      forbiddenTools: ['cascade'],
      llm: { primaryAlias: 'balanced', thinkingEnabled: true, maxTokens: 0 },
      produces: { eventType: 'code.done', schemaDef: {} },
      consumes: { eventTypes: ['task.assigned'], injects: ['repo'] },
      fanIn: { strategy: 'merge', contributesTo: '' },
      yamlSource: '[built-in]',
    });
  });
});

describe('getPersonaYaml', () => {
  it('fetches yaml endpoint', async () => {
    mockFetch.mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => 'name: coding-agent\n',
    });
    const result = await getPersonaYaml('coding-agent');
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/personas/coding-agent/yaml'),
      expect.any(Object)
    );
    expect(result).toBe('name: coding-agent\n');
  });
});

describe('createPersona', () => {
  const req: PersonaCreateRequest = {
    name: 'new-agent',
    systemPromptTemplate: 'You are helpful.',
    allowedTools: ['file'],
    forbiddenTools: [],
    permissionMode: 'read-only',
    iterationBudget: 10,
    llmPrimaryAlias: 'balanced',
    llmThinkingEnabled: false,
    llmMaxTokens: 0,
    producesEventType: '',
    consumesEventTypes: [],
    consumesInjects: [],
    fanInStrategy: 'merge',
    fanInContributesTo: '',
  };

  it('sends POST to /personas', async () => {
    mockOk(rawDetail, 201);
    await createPersona(req);
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/personas'),
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('converts camelCase request to snake_case body', async () => {
    mockOk(rawDetail, 201);
    await createPersona(req);
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body).toMatchObject({
      name: 'new-agent',
      system_prompt_template: 'You are helpful.',
      allowed_tools: ['file'],
      permission_mode: 'read-only',
      iteration_budget: 10,
      llm_primary_alias: 'balanced',
      llm_thinking_enabled: false,
      llm_max_tokens: 0,
    });
  });
});

describe('updatePersona', () => {
  const req: PersonaCreateRequest = {
    name: 'coding-agent',
    systemPromptTemplate: 'Updated.',
    allowedTools: [],
    forbiddenTools: [],
    permissionMode: '',
    iterationBudget: 0,
    llmPrimaryAlias: '',
    llmThinkingEnabled: false,
    llmMaxTokens: 0,
    producesEventType: '',
    consumesEventTypes: [],
    consumesInjects: [],
    fanInStrategy: 'merge',
    fanInContributesTo: '',
  };

  it('sends PUT to /personas/:name', async () => {
    mockOk(rawDetail);
    await updatePersona('coding-agent', req);
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/personas/coding-agent'),
      expect.objectContaining({ method: 'PUT' })
    );
  });
});

describe('deletePersona', () => {
  it('sends DELETE to /personas/:name', async () => {
    mockFetch.mockResolvedValue({ status: 204, ok: true, json: async () => undefined });
    await deletePersona('my-agent');
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/personas/my-agent'),
      expect.objectContaining({ method: 'DELETE' })
    );
  });
});

describe('forkPersona', () => {
  it('sends POST to /personas/:name/fork', async () => {
    mockOk(rawDetail, 201);
    await forkPersona('coding-agent', { newName: 'my-fork' });
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/personas/coding-agent/fork'),
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('sends new_name in request body', async () => {
    mockOk(rawDetail, 201);
    await forkPersona('coding-agent', { newName: 'my-fork' });
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.new_name).toBe('my-fork');
  });
});
