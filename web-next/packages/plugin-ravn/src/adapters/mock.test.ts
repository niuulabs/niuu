import { describe, it, expect } from 'vitest';
import { createMockPersonaStore } from './mock';

const MIGRATION_REVIEWER = `name: migration-reviewer
description: Checks a migration before it runs.
permission_mode: read_only
system_prompt_template: Review the migration.
consumes:
  event_types: [migration.requested]
produces:
  event_type: migration_reviewer.completed
  event_type_map:
    approve: migration_reviewer.approve
  when:
    approve: The migration can run twice.
`;

describe('createMockPersonaStore', () => {
  it('returns the seeded personas with the events their outcomes send', async () => {
    const store = createMockPersonaStore();
    const result = await store.listPersonas();
    const coder = result.find((persona) => persona.name === 'coder');
    expect(result.some((persona) => persona.name === 'architect')).toBe(true);
    expect(coder?.outcomeEvents).toEqual({ done: 'code.changed' });
  });

  it('filters to builtin personas only', async () => {
    const store = createMockPersonaStore();
    const result = await store.listPersonas('builtin');
    expect(result.every((p) => p.isBuiltin)).toBe(true);
    expect(result.length).toBeGreaterThan(0);
  });

  it('returns empty list for custom when only builtins exist', async () => {
    const store = createMockPersonaStore();
    expect(await store.listPersonas('custom')).toHaveLength(0);
  });

  it('getPersona returns a detail read from the persona document', async () => {
    const store = createMockPersonaStore();
    const detail = await store.getPersona('coder');
    expect(detail.name).toBe('coder');
    expect(detail.systemPromptTemplate).toContain('coder');
    expect(detail.produces.when.done).toBeTruthy();
    expect(detail.yamlSource).toBe('[mock]');
    const architect = await store.getPersona('architect');
    expect(architect.overrideSource).toContain('architect');
  });

  it('getPersona and getPersonaYaml throw for unknown persona', async () => {
    const store = createMockPersonaStore();
    await expect(store.getPersona('nonexistent')).rejects.toThrow('Persona not found');
    await expect(store.getPersonaYaml('ghost')).rejects.toThrow('Persona not found');
  });

  it('getPersonaYaml and getPersonaDocument return the same persona', async () => {
    const store = createMockPersonaStore();
    const yaml = await store.getPersonaYaml('coder');
    const doc = await store.getPersonaDocument('coder');
    expect(yaml).toContain('name: coder');
    expect(doc.name).toBe('coder');
    expect(doc.permission_mode).toBe('workspace_write');
  });

  it('checks YAML without saving it', async () => {
    const store = createMockPersonaStore();
    expect(await store.checkPersonaYaml(MIGRATION_REVIEWER)).toEqual({ valid: true, errors: [] });
    expect((await store.checkPersonaYaml('name: [')).valid).toBe(false);
    expect((await store.checkPersonaYaml('description: none\n')).errors[0]).toMatch(/name/);
    expect((await store.listPersonas()).some((p) => p.name === 'migration-reviewer')).toBe(false);
  });

  it('creates a persona from YAML and refuses a name that exists', async () => {
    const store = createMockPersonaStore();
    const detail = await store.createPersonaFromYaml(MIGRATION_REVIEWER);
    expect(detail).toMatchObject({
      name: 'migration-reviewer',
      isBuiltin: false,
      permissionMode: 'safe',
      outcomeEvents: { approve: 'migration_reviewer.approve' },
      consumesEvents: ['migration.requested'],
    });
    expect(detail.produces.when).toEqual({ approve: 'The migration can run twice.' });
    await expect(store.createPersonaFromYaml(MIGRATION_REVIEWER)).rejects.toThrow('already exists');
  });

  it('replaces a persona with its YAML, keeping it a built-in with an override', async () => {
    const store = createMockPersonaStore();
    const yaml = MIGRATION_REVIEWER.replace('migration-reviewer', 'coder');
    const detail = await store.replacePersonaYaml('coder', yaml);
    expect(detail).toMatchObject({ name: 'coder', isBuiltin: true, hasOverride: true });
    await expect(store.replacePersonaYaml('coder', MIGRATION_REVIEWER)).rejects.toThrow(
      'this is coder',
    );
    await expect(store.replacePersonaYaml('ghost', yaml)).rejects.toThrow('Persona not found');
  });

  it('deletePersona removes the persona', async () => {
    const store = createMockPersonaStore();
    await store.deletePersona('architect');
    const all = await store.listPersonas();
    expect(all.some((p) => p.name === 'architect')).toBe(false);
  });

  it('forkPersona creates a non-builtin copy of the whole document', async () => {
    const store = createMockPersonaStore();
    const forked = await store.forkPersona('coder', { newName: 'my-coder' });
    expect(forked.name).toBe('my-coder');
    expect(forked.isBuiltin).toBe(false);
    expect((await store.getPersonaDocument('my-coder')).produces).toEqual(
      (await store.getPersonaDocument('coder')).produces,
    );
  });

  it('forkPersona throws for unknown source', async () => {
    const store = createMockPersonaStore();
    await expect(store.forkPersona('ghost', { newName: 'copy' })).rejects.toThrow();
  });
});
