import { describe, it, expect } from 'vitest';
import {
  createMockPersonaStore,
  createMockRavenStream,
  createMockSessionStream,
  createMockTriggerStore,
  createMockBudgetStream,
  createMockWardenStore,
} from './mock';

// ---------------------------------------------------------------------------
// IPersonaStore mock
// ---------------------------------------------------------------------------

describe('createMockPersonaStore', () => {
  it('returns the seeded personas including the warden curator', async () => {
    const store = createMockPersonaStore();
    const result = await store.listPersonas();
    expect(result.length).toBe(17);
    expect(result.some((persona) => persona.name === 'mimir-warden')).toBe(true);
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

  it('getPersona throws for unknown persona', async () => {
    const store = createMockPersonaStore();
    await expect(store.getPersona('nonexistent')).rejects.toThrow('Persona not found');
  });

  it('getPersonaYaml returns a YAML string', async () => {
    const store = createMockPersonaStore();
    const yaml = await store.getPersonaYaml('coder');
    expect(yaml).toContain('name: coder');
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
      allowedTools: ['read'],
      forbiddenTools: [],
      permissionMode: 'default',
      iterationBudget: 10,
      llmThinkingEnabled: false,
      llmMaxTokens: 4096,
      producesEventType: 'custom.done',
      producesSchema: {},
      consumesEvents: [{ name: 'custom.requested' }],
      consumesSchema: {},
    };
    const detail = await store.createPersona(req);
    expect(detail.name).toBe('my-custom');
    expect(detail.isBuiltin).toBe(false);
    expect(detail.llm.maxTokens).toBe(4096);

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
      allowedTools: ['read', 'write', 'bash'],
      forbiddenTools: [],
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
        allowedTools: [],
        forbiddenTools: [],
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

// ---------------------------------------------------------------------------
// IRavenStream mock
// ---------------------------------------------------------------------------

describe('createMockRavenStream', () => {
  it('returns the seeded fleet', async () => {
    const stream = createMockRavenStream();
    const ravens = await stream.listRavens();
    expect(ravens.length).toBeGreaterThan(0);
    expect(ravens[0]).toHaveProperty('id');
    expect(ravens[0]).toHaveProperty('status');
  });

  it('getRaven returns by id', async () => {
    const stream = createMockRavenStream();
    const ravn = await stream.getRaven('a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c');
    expect(ravn.personaName).toBe('sindri');
  });

  it('getRaven throws for unknown id', async () => {
    const stream = createMockRavenStream();
    await expect(stream.getRaven('ffffffff-ffff-4fff-bfff-ffffffffffff')).rejects.toThrow(
      'Ravn not found',
    );
  });
});

// ---------------------------------------------------------------------------
// ISessionStream mock
// ---------------------------------------------------------------------------

describe('createMockSessionStream', () => {
  it('returns seeded sessions', async () => {
    const stream = createMockSessionStream();
    const sessions = await stream.listSessions();
    expect(sessions.length).toBeGreaterThan(0);
  });

  it('getSession returns by id', async () => {
    const stream = createMockSessionStream();
    const session = await stream.getSession('10000001-0000-4000-8000-000000000001');
    expect(session.personaName).toBe('sindri');
  });

  it('getSession throws for unknown id', async () => {
    const stream = createMockSessionStream();
    await expect(stream.getSession('ffffffff-ffff-4fff-bfff-ffffffffffff')).rejects.toThrow(
      'Session not found',
    );
  });

  it('getMessages returns messages for a session', async () => {
    const stream = createMockSessionStream();
    const messages = await stream.getMessages('10000001-0000-4000-8000-000000000001');
    expect(messages.length).toBeGreaterThan(0);
    expect(messages.every((m) => m.sessionId === '10000001-0000-4000-8000-000000000001')).toBe(
      true,
    );
  });

  it('getMessages returns empty array for session with no messages', async () => {
    const stream = createMockSessionStream();
    const messages = await stream.getMessages('10000001-0000-4000-8000-000000000002');
    expect(messages).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// ITriggerStore mock
// ---------------------------------------------------------------------------

describe('createMockTriggerStore', () => {
  it('returns seeded triggers', async () => {
    const store = createMockTriggerStore();
    const triggers = await store.listTriggers();
    expect(triggers.length).toBe(10);
  });

  it('createTrigger adds a trigger and returns it', async () => {
    const store = createMockTriggerStore();
    const trigger = await store.createTrigger({
      kind: 'manual',
      personaName: 'architect',
      spec: 'run-audit',
      enabled: true,
    });
    expect(trigger.id).toBeDefined();
    expect(trigger.personaName).toBe('architect');
    expect(trigger.createdAt).toBeDefined();

    const all = await store.listTriggers();
    expect(all.length).toBe(11);
  });

  it('deleteTrigger removes a trigger', async () => {
    const store = createMockTriggerStore();
    await store.deleteTrigger('aa000001-0000-4000-8000-000000000001');
    const all = await store.listTriggers();
    expect(all.length).toBe(9);
  });
});

// ---------------------------------------------------------------------------
// IBudgetStream mock
// ---------------------------------------------------------------------------

describe('createMockBudgetStream', () => {
  it('getBudget returns a BudgetState for a known ravn', async () => {
    const stream = createMockBudgetStream();
    const budget = await stream.getBudget('a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c');
    expect(budget.spentUsd).toBeGreaterThanOrEqual(0);
    expect(budget.capUsd).toBeGreaterThan(0);
    expect(budget.warnAt).toBe(0.7);
  });

  it('getBudget returns a default for unknown ravn', async () => {
    const stream = createMockBudgetStream();
    const budget = await stream.getBudget('ffffffff-ffff-4fff-bfff-ffffffffffff');
    expect(budget.spentUsd).toBe(0);
    expect(budget.capUsd).toBe(5.0);
  });

  it('getFleetBudget sums all ravens', async () => {
    const stream = createMockBudgetStream();
    const fleet = await stream.getFleetBudget();
    expect(fleet.spentUsd).toBeGreaterThan(0);
    expect(fleet.capUsd).toBeGreaterThan(0);
    expect(fleet.warnAt).toBe(0.7);
  });
});

// ---------------------------------------------------------------------------
// IWardenStore mock
// ---------------------------------------------------------------------------

describe('createMockWardenStore', () => {
  it('lists and fetches seeded wardens', async () => {
    const store = createMockWardenStore();
    const wardens = await store.listWardens();
    expect(wardens.length).toBeGreaterThan(0);

    const detail = await store.getWarden(wardens[0]!.id);
    expect(detail.id).toBe(wardens[0]!.id);

    await expect(store.getWarden('missing')).rejects.toThrow(/Warden not found/i);
  });

  it('creates a unique warden id and emits subscription updates', async () => {
    const store = createMockWardenStore();
    const first = await store.createWarden({
      name: 'Mimir Keeper',
      deployment: 'launchd',
      mountNames: ['local'],
    });
    const second = await store.createWarden({
      name: 'Mimir Keeper',
      deployment: 'k8s-cronjob',
      mountNames: ['shared'],
      features: { autoDream: false },
      schedules: { dream: '0 0 * * 0' },
      console: { shell: 'zsh' },
      autostart: true,
      createdBy: 'operator-2',
    });

    expect(first.id).toBe('mimir-keeper');
    expect(second.id).toBe('mimir-keeper-2');
    expect(second.features.autoDream).toBe(false);
    expect(second.schedules.dream).toBe('0 0 * * 0');
    expect(second.console.shell).toBe('zsh');
    expect(second.autostart).toBe(true);
    expect(second.createdBy).toBe('operator-2');

    const seen: string[] = [];
    const unsubscribe = store.subscribeWarden(second.id, (warden) => {
      seen.push(warden.runtime?.state ?? 'unknown');
    });

    await store.observeWarden(second.id);
    await store.installWarden(second.id);
    await store.startWarden(second.id);
    await store.stopWarden(second.id);
    await store.uninstallWarden(second.id);
    unsubscribe();

    expect(seen).toEqual(['offline', 'idle', 'active', 'idle', 'offline']);
  });

  it('observes deployment sources and handles install/start/stop/uninstall transitions', async () => {
    const store = createMockWardenStore();
    const created = await store.createWarden({
      name: 'K8s keeper',
      deployment: 'k8s-deployment',
      mountNames: ['shared'],
    });

    const observed = await store.observeWarden(created.id);
    expect(observed.supervisor?.observation?.status).toBe('missing');
    expect(observed.supervisor?.observation?.source).toBe('mock-kubernetes');

    const installed = await store.installWarden(created.id);
    expect(installed.supervisor?.installed).toBe(true);
    expect(installed.runtime?.state).toBe('idle');

    const started = await store.startWarden(created.id);
    expect(started.runtime?.state).toBe('active');
    expect(started.runtime?.lastStartedAt).toBeDefined();

    const observedActive = await store.observeWarden(created.id);
    expect(observedActive.supervisor?.observation?.status).toBe('running');

    const stopped = await store.stopWarden(created.id);
    expect(stopped.runtime?.state).toBe('idle');

    const uninstalled = await store.uninstallWarden(created.id);
    expect(uninstalled.runtime?.state).toBe('offline');
    expect(uninstalled.supervisor?.installed).toBe(false);
  });

  it('rejects start and stop when a warden has not been installed', async () => {
    const store = createMockWardenStore();
    const created = await store.createWarden({ name: 'Cold keeper' });

    await expect(store.startWarden(created.id)).rejects.toThrow(/must be installed/i);
    await expect(store.stopWarden(created.id)).rejects.toThrow(/must be installed/i);
  });

  it('returns logs and activity shaped by the runtime state', async () => {
    const store = createMockWardenStore();
    const created = await store.createWarden({ name: 'Logger keeper' });
    await store.installWarden(created.id);
    await store.startWarden(created.id);

    const logs = await store.getWardenLogs(created.id, { stream: 'stderr' });
    expect(logs[0]).toMatchObject({
      id: `${created.id}-stderr-1`,
      source: 'stderr',
      logger: 'ravn.daemon',
    });

    const activeActivity = await store.getWardenActivity(created.id);
    expect(activeActivity[0]).toMatchObject({
      level: 'INFO',
      message: expect.stringContaining('dream cycle scheduled'),
    });

    await store.stopWarden(created.id);
    const idleActivity = await store.getWardenActivity(created.id);
    expect(idleActivity[0]).toMatchObject({
      level: 'WARN',
      message: 'warden is installed but idle',
    });

    await expect(store.getWardenLogs('missing')).rejects.toThrow(/Warden not found/i);
    await expect(store.getWardenActivity('missing')).rejects.toThrow(/Warden not found/i);
  });
});
