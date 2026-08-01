import { describe, it, expect, vi } from 'vitest';
import {
  createMockRegistryRepository,
  createMockTopologyStream,
  createMockEventStream,
  createMockAgentDirectory,
} from './mock';

describe('createMockRegistryRepository', () => {
  it('returns a registry with the correct version', async () => {
    const repo = createMockRegistryRepository();
    const registry = await repo.getRegistry();
    expect(registry.version).toBe(7);
    expect(registry.updatedAt).toBe('2026-04-15T09:24:11Z');
  });

  it('returns all 17 entity types', async () => {
    const repo = createMockRegistryRepository();
    const registry = await repo.getRegistry();
    expect(registry.types.length).toBe(17);
  });

  it('includes the four named domain entities', async () => {
    const repo = createMockRegistryRepository();
    const { types } = await repo.getRegistry();
    const ids = types.map((t) => t.id);
    expect(ids).toContain('realm');
    expect(ids).toContain('cluster');
    expect(ids).toContain('host');
    expect(ids).toContain('run');
  });

  it('every entity type has required fields', async () => {
    const repo = createMockRegistryRepository();
    const { types } = await repo.getRegistry();
    for (const t of types) {
      expect(t.id).toBeTruthy();
      expect(t.label).toBeTruthy();
      expect(t.rune).toBeTruthy();
      expect(t.shape).toBeTruthy();
      expect(Array.isArray(t.canContain)).toBe(true);
      expect(Array.isArray(t.parentTypes)).toBe(true);
      expect(Array.isArray(t.fields)).toBe(true);
    }
  });

  it('persists registry replacements in memory', async () => {
    const repo = createMockRegistryRepository();
    const registry = await repo.getRegistry();
    const saved = await repo.saveRegistry({
      ...registry,
      types: registry.types.slice(0, 1),
    });
    expect(saved.types).toHaveLength(1);
    expect((await repo.getRegistry()).types).toHaveLength(1);
  });

  it('covers all 5 edge kinds across entity types', async () => {
    // Verify the topology stream edges include all 5 kinds
    const stream = createMockTopologyStream();
    const snapshot = stream.getSnapshot();
    expect(snapshot).not.toBeNull();
    const kinds = new Set(snapshot!.edges.map((e) => e.kind));
    expect(kinds.has('solid')).toBe(true);
    expect(kinds.has('dashed-anim')).toBe(true);
    expect(kinds.has('dashed-long')).toBe(true);
    expect(kinds.has('soft')).toBe(true);
    expect(kinds.has('run')).toBe(true);
  });
});

describe('createMockTopologyStream', () => {
  it('getSnapshot returns a topology with nodes and edges', () => {
    const stream = createMockTopologyStream();
    const snapshot = stream.getSnapshot();
    expect(snapshot).not.toBeNull();
    expect(snapshot!.nodes.length).toBeGreaterThan(0);
    expect(snapshot!.edges.length).toBeGreaterThan(0);
    expect(typeof snapshot!.timestamp).toBe('string');
  });

  it('topology includes realm, cluster, host, and run nodes', () => {
    const stream = createMockTopologyStream();
    const { nodes } = stream.getSnapshot()!;
    const typeIds = new Set(nodes.map((n) => n.typeId));
    expect(typeIds.has('realm')).toBe(true);
    expect(typeIds.has('cluster')).toBe(true);
    expect(typeIds.has('host')).toBe(true);
    expect(typeIds.has('run')).toBe(true);
  });

  it('every node has required base fields', () => {
    const stream = createMockTopologyStream();
    const { nodes } = stream.getSnapshot()!;
    for (const node of nodes) {
      expect(node.id).toBeTruthy();
      expect(node.typeId).toBeTruthy();
      expect(node.label).toBeTruthy();
      expect(node.status).toBeTruthy();
    }
  });

  it('ting node has kind-specific fields', () => {
    const stream = createMockTopologyStream();
    const { nodes } = stream.getSnapshot()!;
    const ting = nodes.find((n) => n.typeId === 'ting');
    expect(ting).toBeDefined();
    expect(ting!.mode).toBe('active');
    expect(ting!.activeSagas).toBeGreaterThanOrEqual(0);
    expect(ting!.pendingRuns).toBeGreaterThanOrEqual(0);
  });

  it('bifrost node has kind-specific fields', () => {
    const stream = createMockTopologyStream();
    const { nodes } = stream.getSnapshot()!;
    const bifrost = nodes.find((n) => n.typeId === 'bifrost');
    expect(bifrost).toBeDefined();
    expect(Array.isArray(bifrost!.providers)).toBe(true);
    expect(typeof bifrost!.reqPerMin).toBe('number');
    expect(typeof bifrost!.cacheHitRate).toBe('number');
  });

  it('volundr node has kind-specific fields', () => {
    const stream = createMockTopologyStream();
    const { nodes } = stream.getSnapshot()!;
    const volundr = nodes.find((n) => n.typeId === 'volundr');
    expect(volundr).toBeDefined();
    expect(typeof volundr!.activeSessions).toBe('number');
    expect(typeof volundr!.maxSessions).toBe('number');
  });

  it('ravn_long node has kind-specific fields', () => {
    const stream = createMockTopologyStream();
    const { nodes } = stream.getSnapshot()!;
    const ravn = nodes.find((n) => n.typeId === 'ravn_long');
    expect(ravn).toBeDefined();
    expect(ravn!.persona).toBeTruthy();
    expect(ravn!.specialty).toBeTruthy();
    expect(typeof ravn!.tokens).toBe('number');
  });

  it('host node has kind-specific fields', () => {
    const stream = createMockTopologyStream();
    const { nodes } = stream.getSnapshot()!;
    const host = nodes.find((n) => n.typeId === 'host');
    expect(host).toBeDefined();
    expect(host!.hw).toBeTruthy();
    expect(host!.os).toBeTruthy();
  });

  it('realm node has vlan and dns fields', () => {
    const stream = createMockTopologyStream();
    const { nodes } = stream.getSnapshot()!;
    const realm = nodes.find((n) => n.typeId === 'realm');
    expect(realm).toBeDefined();
    expect(typeof realm!.vlan).toBe('number');
    expect(realm!.dns).toBeTruthy();
  });

  it('subscribe immediately calls listener with current snapshot', () => {
    const stream = createMockTopologyStream();
    const listener = vi.fn();
    stream.subscribe(listener);
    expect(listener).toHaveBeenCalledOnce();
    expect(listener).toHaveBeenCalledWith(expect.objectContaining({ nodes: expect.any(Array) }));
  });

  it('unsubscribe removes listener', () => {
    const stream = createMockTopologyStream();
    const listener = vi.fn();
    const unsub = stream.subscribe(listener);
    expect(listener).toHaveBeenCalledOnce();
    unsub();
    // After unsubscribe, listener count should be 0
    const listener2 = vi.fn();
    stream.subscribe(listener2);
    expect(listener).toHaveBeenCalledOnce(); // still only once
    expect(listener2).toHaveBeenCalledOnce();
  });
});

describe('createMockEventStream', () => {
  it('subscribe emits seed events synchronously', () => {
    const eventStream = createMockEventStream();
    const received: string[] = [];
    eventStream.subscribe((ev) => received.push(ev.id));
    expect(received.length).toBe(5);
    expect(received[0]).toBe('ev-1');
  });

  it('every event has required fields (web2 format)', () => {
    const eventStream = createMockEventStream();
    eventStream.subscribe((ev) => {
      expect(ev.id).toBeTruthy();
      expect(ev.time).toBeTruthy();
      expect(ev.type).toBeTruthy();
      expect(ev.subject).toBeTruthy();
      expect(ev.body).toBeTruthy();
      expect(['RUN', 'RAVN', 'TING', 'MIMIR', 'BIFROST']).toContain(ev.type);
    });
  });

  it('unsubscribe returns without error', () => {
    const eventStream = createMockEventStream();
    const unsub = eventStream.subscribe(() => {});
    expect(() => unsub()).not.toThrow();
  });

  it('includes events of varying types', () => {
    const eventStream = createMockEventStream();
    const types = new Set<string>();
    eventStream.subscribe((ev) => types.add(ev.type));
    expect(types.has('RAVN')).toBe(true);
    expect(types.has('BIFROST')).toBe(true);
    expect(types.has('MIMIR')).toBe(true);
  });
});

// ── Agent directory ───────────────────────────────────────────────────────────

describe('createMockAgentDirectory', () => {
  it('projects every agent-bearing topology node and nothing else', async () => {
    const page = await createMockAgentDirectory().listAgents();
    expect(page.items.map((a) => a.sourceAgentId).sort()).toEqual([
      'ravn-huginn',
      'ravn-muninn',
      'run-0',
    ]);
  });

  it('maps node types onto directory kinds', async () => {
    const page = await createMockAgentDirectory().listAgents();
    const kinds = Object.fromEntries(page.items.map((a) => [a.sourceAgentId, a.kind]));
    expect(kinds['ravn-huginn']).toBe('resident');
    expect(kinds['run-0']).toBe('workflow-session');
  });

  it('links each entry back to the topology node it projects', async () => {
    const directory = createMockAgentDirectory();
    const page = await directory.listAgents();
    const topology = createMockTopologyStream().getSnapshot();
    for (const entry of page.items) {
      expect(topology?.nodes.some((n) => n.id === entry.topologyNodeId)).toBe(true);
    }
  });

  it('reports a healthy source and a complete page', async () => {
    const page = await createMockAgentDirectory().listAgents();
    expect(page.partial).toBe(false);
    expect(page.warnings).toEqual([]);
    expect(page.sources[0]?.status).toBe('healthy');
  });

  it('filters by kind', async () => {
    const page = await createMockAgentDirectory().listAgents({ kinds: ['workflow-session'] });
    expect(page.items.map((a) => a.sourceAgentId)).toEqual(['run-0']);
  });

  it('filters by skill', async () => {
    const page = await createMockAgentDirectory().listAgents({ skills: ['recall_context'] });
    expect(page.items.map((a) => a.sourceAgentId)).toEqual(['ravn-muninn']);
  });

  it('filters by tag, cluster and instance', async () => {
    const directory = createMockAgentDirectory();
    expect((await directory.listAgents({ tags: ['resident'] })).items).toHaveLength(2);
    expect((await directory.listAgents({ clusterIds: ['nope'] })).items).toHaveLength(0);
    expect(
      (await directory.listAgents({ instanceIds: ['observatory-valaskjalf'] })).items.length,
    ).toBeGreaterThan(0);
  });

  it('filters by observed status', async () => {
    const page = await createMockAgentDirectory().listAgents({ statuses: ['observing'] });
    expect(page.items.map((a) => a.sourceAgentId)).toEqual(['run-0']);
  });

  it('ands the filters together', async () => {
    const page = await createMockAgentDirectory().listAgents({
      kinds: ['resident'],
      skills: ['recall_context'],
    });
    expect(page.items.map((a) => a.sourceAgentId)).toEqual(['ravn-muninn']);
  });

  it('treats an empty filter array as unfiltered', async () => {
    const page = await createMockAgentDirectory().listAgents({ kinds: [], skills: [] });
    expect(page.items).toHaveLength(3);
  });

  it('carries a usable A2A card surface on every entry', async () => {
    const entry = await createMockAgentDirectory().getAgent('ravn-huginn');
    expect(entry.cardUrl).toMatch(/\.well-known\/agent-card\.json$/);
    expect(entry.supportedInterfaces[0]?.protocolVersion).toBe('0.3.0');
    expect(entry.capabilities).toMatchObject({ streaming: true });
    expect(entry.skillIds.length).toBeGreaterThan(0);
  });

  it('resolves an agent by directory id as well as source id', async () => {
    const directory = createMockAgentDirectory();
    expect((await directory.getAgent('agent-ravn-huginn')).name).toBe('huginn');
    expect((await directory.getAgent('ravn-huginn')).name).toBe('huginn');
  });

  it('throws for an unknown agent rather than returning empty', async () => {
    await expect(createMockAgentDirectory().getAgent('nope')).rejects.toThrow(
      'Unknown agent: nope',
    );
  });

  it('hands out copies so callers cannot mutate the seed', async () => {
    const directory = createMockAgentDirectory();
    const first = await directory.getAgent('ravn-huginn');
    first.name = 'mutated';
    expect((await directory.getAgent('ravn-huginn')).name).toBe('huginn');
  });
});
