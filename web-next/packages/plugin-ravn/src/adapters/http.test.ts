/**
 * HTTP adapter tests — adapted from web/src/modules/ravn/api/client.test.ts
 */
import { describe, it, expect, vi } from 'vitest';
import { buildRavnPersonaAdapter } from './http';
import type { IPersonaStore } from '../ports';

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
  iteration_budget: 40,
  is_builtin: true,
  has_override: false,
  produces_event: 'code.changed',
  outcome_events: { pass: 'review.passed', fail: 'review.changes_requested' },
  consumes_events: ['code.requested'],
};

const rawDetail = {
  ...rawSummary,
  description: 'Full coding agent description',
  system_prompt_template: 'You are a coder.',
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
      iterationBudget: 40,
      isBuiltin: true,
      hasOverride: false,
      producesEvent: 'code.changed',
      outcomeEvents: { pass: 'review.passed', fail: 'review.changes_requested' },
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
      llm: { thinkingEnabled: true, maxTokens: 8192 },
      produces: { eventType: 'code.changed', schemaDef: { file: 'string' } },
      consumes: { events: [{ name: 'code.requested' }], schemaDef: { topic: 'string' } },
      fanIn: { strategy: 'merge', params: {} },
      yamlSource: '[built-in]',
      overrideSource: '[user:user-1]',
    });
  });

  it('leaves fanIn undefined when the API returns fan_in null', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({ ...rawDetail, fan_in: null });
    const result = await buildRavnPersonaAdapter(client).getPersona('coder');
    expect(result.fanIn).toBeUndefined();
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
// Persona documents
// ---------------------------------------------------------------------------

describe('persona documents', () => {
  const yaml = 'name: new-agent\nsystem_prompt_template: You are helpful.\n';

  it('reads the document from the portable route', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({ id: 'coder', definition: { name: 'coder' } });
    const result = await buildRavnPersonaAdapter(client).getPersonaDocument('coder');
    expect(client.get).toHaveBeenCalledWith('/personas/coder/portable');
    expect(result).toEqual({ name: 'coder' });
  });

  it('checks YAML without saving it', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({ valid: false, errors: ['no'] });
    const result = await buildRavnPersonaAdapter(client).checkPersonaYaml(yaml);
    expect(client.post).toHaveBeenCalledWith('/personas/yaml/check', { yaml });
    expect(result).toEqual({ valid: false, errors: ['no'] });
  });

  it('creates from YAML and returns a camelCase PersonaDetail', async () => {
    const client = makeClient();
    client.post.mockResolvedValue(rawDetail);
    const result = await buildRavnPersonaAdapter(client).createPersonaFromYaml(yaml);
    expect(client.post).toHaveBeenCalledWith('/personas/yaml', { yaml });
    expect(result.systemPromptTemplate).toBe('You are a coder.');
  });

  it('replaces a persona with its YAML', async () => {
    const client = makeClient();
    client.put.mockResolvedValue({
      ...rawDetail,
      produces: { ...rawDetail.produces, when: { pass: 'It works.' } },
    });
    const result = await buildRavnPersonaAdapter(client).replacePersonaYaml('my agent', yaml);
    expect(client.put).toHaveBeenCalledWith('/personas/my%20agent/yaml', { yaml });
    expect(result.produces.when).toEqual({ pass: 'It works.' });
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
    expect(typeof store.getPersonaDocument).toBe('function');
    expect(typeof store.createPersonaFromYaml).toBe('function');
    expect(typeof store.replacePersonaYaml).toBe('function');
    expect(typeof store.deletePersona).toBe('function');
    expect(typeof store.forkPersona).toBe('function');
  });
});
