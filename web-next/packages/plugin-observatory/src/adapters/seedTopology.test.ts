import { describe, it, expect } from 'vitest';
import { SEED_NODES, SEED_EDGES, SEED_TOPOLOGY } from './seedTopology';
import { createMockRegistryRepository } from './mock';
import { deriveAgentMeshes } from '../domain/agentMesh';
import type { EntityType } from '../domain';

const byId = new Map(SEED_NODES.map((n) => [n.id, n]));

describe('seed topology structure', () => {
  it('gives every node a unique id', () => {
    expect(new Set(SEED_NODES.map((n) => n.id)).size).toBe(SEED_NODES.length);
  });

  it('gives every edge a unique id', () => {
    expect(new Set(SEED_EDGES.map((e) => e.id)).size).toBe(SEED_EDGES.length);
  });

  it('resolves every parent reference', () => {
    for (const node of SEED_NODES) {
      if (node.parentId === null) continue;
      expect(byId.has(node.parentId), `${node.id} -> ${node.parentId}`).toBe(true);
    }
  });

  it('resolves both endpoints of every edge', () => {
    for (const e of SEED_EDGES) {
      expect(byId.has(e.sourceId), `${e.id} source ${e.sourceId}`).toBe(true);
      expect(byId.has(e.targetId), `${e.id} target ${e.targetId}`).toBe(true);
    }
  });

  it('roots realms and vendor clouds, and nothing else', () => {
    // A vendor cloud is deliberately parentless: hosted models are not in any
    // realm of ours, which is the whole reason they are drawn out here.
    const roots = SEED_NODES.filter((n) => n.parentId === null);
    expect(new Set(roots.map((n) => n.typeId))).toEqual(new Set(['realm', 'cloud']));
    expect(roots.filter((n) => n.typeId === 'realm')).toHaveLength(5);
    expect(roots.filter((n) => n.typeId === 'cloud')).toHaveLength(3);
  });

  it('puts every hosted model in a cloud and every self-hosted one in a cluster', () => {
    const models = SEED_NODES.filter((n) => n.typeId === 'model');
    expect(models.length).toBeGreaterThan(0);
    for (const model of models) {
      const parent = byId.get(model.parentId ?? '');
      expect(parent, `${model.id} has no parent`).toBeDefined();
      expect(parent?.typeId).toBe(model.location === 'external' ? 'cloud' : 'bifrost');
    }
  });

  it('contains no parent cycles', () => {
    for (const node of SEED_NODES) {
      const seen = new Set<string>([node.id]);
      let current = node.parentId;
      while (current) {
        expect(seen.has(current), `cycle through ${node.id}`).toBe(false);
        seen.add(current);
        current = byId.get(current)?.parentId ?? null;
      }
    }
  });

  it('obeys the registry parentTypes for every node', async () => {
    const registry = await createMockRegistryRepository().getRegistry();
    const types = new Map<string, EntityType>(registry.types.map((t) => [t.id, t]));

    for (const node of SEED_NODES) {
      const type = types.get(node.typeId);
      expect(type, `unknown typeId ${node.typeId} on ${node.id}`).toBeDefined();
      if (!type || node.parentId === null) continue;
      const parent = byId.get(node.parentId);
      expect(
        type.parentTypes.includes(parent?.typeId ?? ''),
        `${node.id} (${node.typeId}) under ${parent?.typeId}`,
      ).toBe(true);
    }
  });
});

describe('seed topology content', () => {
  const count = (typeId: string) => SEED_NODES.filter((n) => n.typeId === typeId).length;

  it('spans five realms and eight clusters', () => {
    expect(count('realm')).toBe(5);
    expect(count('cluster')).toBe(8);
  });

  it('places a resident in every cluster', () => {
    const clusters = SEED_NODES.filter((n) => n.typeId === 'cluster').map((n) => n.id);
    const residentParents = new Set(
      SEED_NODES.filter((n) => n.typeId === 'ravn_long').map((n) => n.parentId),
    );
    for (const cluster of clusters) {
      expect(residentParents.has(cluster), `no resident in ${cluster}`).toBe(true);
    }
  });

  it('runs two residents directly on bare metal, outside kubernetes', () => {
    const onHosts = SEED_NODES.filter(
      (n) => n.typeId === 'ravn_long' && byId.get(n.parentId ?? '')?.typeId === 'host',
    );
    expect(onHosts.map((n) => n.label).sort()).toEqual(['eldhrímnir', 'víðarr']);
  });

  it('carries several distinct knowledge Mimir instances', () => {
    const mimirs = SEED_NODES.filter((n) => n.typeId === 'mimir');
    expect(mimirs.length).toBeGreaterThanOrEqual(3);
    // Each lives in a different cluster — they are separate stores, not replicas.
    expect(new Set(mimirs.map((n) => n.parentId)).size).toBe(mimirs.length);
  });

  it('models the metrics store as a service, never as a knowledge Mimir', () => {
    const metrics = SEED_NODES.find((n) => n.id === 'svc-mimir-metrics');
    expect(metrics?.typeId).toBe('service');
    expect(metrics?.purpose).toMatch(/not a knowledge base/i);
  });

  it('marks the unreachable cluster and its Mimir as unverified rather than healthy', () => {
    expect(byId.get('cluster-valhalla')?.status).toBe('unknown');
    expect(byId.get('mimir-valhalla')?.status).toBe('unknown');
    const declared = SEED_EDGES.filter((e) => e.confidence === 'declared');
    expect(declared.length).toBeGreaterThan(0);
  });

  it('exposes three DGX Sparks with GPUs', () => {
    const sparks = SEED_NODES.filter((n) => n.typeId === 'host' && n.hw === 'DGX Spark');
    expect(sparks).toHaveLength(3);
    expect(sparks.every((n) => n.gpu === 'GB10')).toBe(true);
  });

  it('runs two workflow sessions, each with its own run agents', () => {
    const runs = SEED_NODES.filter((n) => n.typeId === 'run');
    expect(runs).toHaveLength(2);
    for (const run of runs) {
      const agents = SEED_NODES.filter((n) => n.typeId === 'ravn_run' && n.parentId === run.id);
      expect(agents.length).toBeGreaterThanOrEqual(4);
    }
  });

  it('forms agent meshes that span clusters', () => {
    const meshes = deriveAgentMeshes(SEED_TOPOLOGY);
    expect(meshes.length).toBeGreaterThanOrEqual(3);

    const spanning = meshes.find((m) => m.id === 'ops-mesh');
    expect(spanning).toBeDefined();
    const clusters = new Set(
      spanning?.memberIds.map((id) => byId.get(id)?.parentId).filter(Boolean),
    );
    expect(clusters.size).toBeGreaterThan(1);
  });
});
