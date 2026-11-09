import { describe, it, expect } from 'vitest';
import { createMockPersonaStore } from './mock';

// ---------------------------------------------------------------------------
// IPersonaStore mock
// ---------------------------------------------------------------------------

describe('createMockPersonaStore', () => {
  it('returns the seeded personas', async () => {
    const store = createMockPersonaStore();
    const result = await store.listPersonas();
    expect(result.length).toBeGreaterThan(0);
    expect(result.some((persona) => persona.name === 'architect')).toBe(true);
  });

  it('filters to builtin personas only', async () => {
    const store = createMockPersonaStore();
    const result = await store.listPersonas('builtin');
    expect(result.every((p) => p.isBuiltin)).toBe(true);
    expect(result.length).toBeGreaterThan(0);
  });

  it('returns empty list for custom when only builtins exist', async () => {
    const store = createMockPersonaStore();
    const result = await store.listPersonas('custom');
    expect(result).toHaveLength(0);
  });

  it('getPersona returns a PersonaDetail', async () => {
    const store = createMockPersonaStore();
    const detail = await store.getPersona('coder');
    expect(detail.name).toBe('coder');
    expect(detail.systemPromptTemplate).toBeDefined();
    expect(detail.llm.maxTokens).toBeGreaterThan(0);
    expect(detail.fanIn.strategy).toBeDefined();
    expect(detail.yamlSource).toBe('[mock]');
  });

  it('builds reviewer defaults and exposes configured override sources', async () => {
    const store = createMockPersonaStore();
    const reviewer = await store.getPersona('reviewer');
    expect(reviewer).toMatchObject({
      llm: { thinkingEnabled: true, maxTokens: 8192 },
      produces: { schemaDef: { verdict: 'string', confidence: 'number', findings: 'array' } },
      consumes: { schemaDef: { diff: 'string', scope: 'string' } },
      fanIn: { strategy: 'merge', params: { mode: 'review.final' } },
      mimirWriteRouting: 'local',
    });
    expect(reviewer.description).toContain('Careful code reviewer');
    expect(reviewer.systemPromptTemplate).toContain('operational risk');
    expect(reviewer.consumes.events).toHaveLength(2);

    const architect = await store.getPersona('architect');
    expect(architect.overrideSource).toContain('overrides/architect.yaml');
  });

  it('getPersona throws for unknown persona', async () => {
    const store = createMockPersonaStore();
    await expect(store.getPersona('nonexistent')).rejects.toThrow('Persona not found');
  });

  it('getPersonaYaml returns a YAML string', async () => {
    const store = createMockPersonaStore();
    const yaml = await store.getPersonaYaml('coder');
    expect(yaml).toContain('name: coder');
  });

  it('serializes an empty produced event as null', async () => {
    const store = createMockPersonaStore();
    const yaml = await store.getPersonaYaml('autonomous-agent');
    expect(yaml).toContain('event: null');
  });

  it('getPersonaYaml throws for unknown persona', async () => {
    const store = createMockPersonaStore();
    await expect(store.getPersonaYaml('ghost')).rejects.toThrow();
  });

  it('createPersona adds the persona and returns a detail', async () => {
    const store = createMockPersonaStore();
    const req = {
      name: 'my-custom',
      role: 'build' as const,
      letter: 'M',
      color: 'var(--color-accent-cyan)',
      summary: 'Custom persona',
      description: 'Custom description',
      systemPromptTemplate: 'Custom system prompt',
      permissionMode: 'default',
      iterationBudget: 10,
      llmThinkingEnabled: false,
      llmMaxTokens: 4096,
      llmTemperature: 0.2,
      producesEventType: 'custom.done',
      producesSchema: {},
      consumesEvents: [{ name: 'custom.requested' }],
      consumesSchema: {},
      fanInStrategy: 'quorum',
      fanInParams: { quorum: 2 },
      mimirWriteRouting: 'shared' as const,
    };
    const detail = await store.createPersona(req);
    expect(detail.name).toBe('my-custom');
    expect(detail.isBuiltin).toBe(false);
    expect(detail.llm.maxTokens).toBe(4096);
    expect(detail.llm.temperature).toBe(0.2);
    expect(detail.fanIn).toEqual({ strategy: 'quorum', params: { quorum: 2 } });
    expect(detail.mimirWriteRouting).toBe('shared');

    const all = await store.listPersonas();
    expect(all.some((p) => p.name === 'my-custom')).toBe(true);
  });

  it('updatePersona modifies an existing persona', async () => {
    const store = createMockPersonaStore();
    const req = {
      name: 'coder',
      role: 'build' as const,
      letter: 'C',
      color: 'var(--color-accent-indigo)',
      summary: 'Updated coder',
      description: 'Updated description',
      systemPromptTemplate: 'Updated prompt',
      permissionMode: 'default',
      iterationBudget: 50,
      llmThinkingEnabled: true,
      llmMaxTokens: 16384,
      producesEventType: 'code.changed',
      producesSchema: {},
      consumesEvents: [{ name: 'code.requested' }],
      consumesSchema: {},
      fanInStrategy: 'any_passes' as const,
    };
    const detail = await store.updatePersona('coder', req);
    expect(detail.iterationBudget).toBe(50);
    expect(detail.llm.maxTokens).toBe(16384);
  });

  it('updatePersona throws for unknown persona', async () => {
    const store = createMockPersonaStore();
    await expect(
      store.updatePersona('ghost', {
        name: 'ghost',
        role: 'build',
        letter: 'G',
        color: '',
        summary: '',
        description: '',
        systemPromptTemplate: '',
        permissionMode: 'default',
        iterationBudget: 0,
        llmThinkingEnabled: false,
        llmMaxTokens: 0,
        producesEventType: '',
        producesSchema: {},
        consumesEvents: [],
        consumesSchema: {},
      }),
    ).rejects.toThrow('Persona not found');
  });

  it('deletePersona removes the persona', async () => {
    const store = createMockPersonaStore();
    await store.deletePersona('architect');
    const all = await store.listPersonas();
    expect(all.some((p) => p.name === 'architect')).toBe(false);
  });

  it('forkPersona creates a non-builtin copy', async () => {
    const store = createMockPersonaStore();
    const forked = await store.forkPersona('coder', { newName: 'my-coder' });
    expect(forked.name).toBe('my-coder');
    expect(forked.isBuiltin).toBe(false);

    const all = await store.listPersonas();
    expect(all.some((p) => p.name === 'my-coder')).toBe(true);
  });

  it('forkPersona throws for unknown source', async () => {
    const store = createMockPersonaStore();
    await expect(store.forkPersona('ghost', { newName: 'copy' })).rejects.toThrow();
  });
});
