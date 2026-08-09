import { describe, expect, it } from 'vitest';
import type { TopologyNode } from '../../domain';
import { nodeFormOf, partsOf, type NodeForm } from './nodeForm';

function node(typeId: string, extra: Partial<TopologyNode> = {}): TopologyNode {
  return { id: typeId, typeId, label: typeId, parentId: null, status: 'healthy', ...extra };
}

describe('nodeFormOf', () => {
  it('splits a host on what it is for, never on its name', () => {
    // The fact somebody is actually looking for is "does it have GPUs", and it
    // is buried if every machine is the same slab.
    expect(nodeFormOf(node('host', { gpu: 'A100 x4' }))).toBe('host-gpu');
    expect(nodeFormOf(node('host', { ...({ roles: ['control-plane'] } as object) }))).toBe(
      'host-control',
    );
    expect(nodeFormOf(node('host'))).toBe('host-cpu');
    // A name that says gpu proves nothing; the capacity does.
    expect(nodeFormOf({ ...node('host'), label: 'k8s-gpuworker-03' })).toBe('host-cpu');
  });

  it('gives a control-plane machine its mast even when it also carries GPUs', () => {
    // A machine that schedules is that first, whatever else is bolted to it.
    const scheduler = node('host', { gpu: 'A100', ...({ roles: ['control-plane'] } as object) });
    expect(nodeFormOf(scheduler)).toBe('host-control');
  });

  it('gives every kind of agent the same body', () => {
    for (const typeId of ['ravn_long', 'ravn_run', 'valkyrie', 'resident']) {
      expect(nodeFormOf(node(typeId))).toBe('agent');
    }
  });

  it('reads the rest of the estate into its families', () => {
    expect(nodeFormOf(node('run'))).toBe('session');
    expect(nodeFormOf(node('model'))).toBe('model');
    expect(nodeFormOf(node('mimir'))).toBe('store');
    expect(nodeFormOf(node('printer'))).toBe('device');
    expect(nodeFormOf(node('gate'))).toBe('step');
    expect(nodeFormOf(node('bifrost'))).toBe('service');
  });

  it('gives a type it has never met a body rather than nothing', () => {
    expect(nodeFormOf(node('brand-new'))).toBe('service');
  });
});

describe('partsOf', () => {
  const FORMS: NodeForm[] = [
    'agent',
    'host-gpu',
    'host-control',
    'host-cpu',
    'session',
    'model',
    'service',
    'step',
    'device',
  ];

  it('builds every form out of at least one solid', () => {
    for (const form of FORMS) expect(partsOf(form).length).toBeGreaterThan(0);
  });

  it('keeps the family resemblance between the three kinds of machine', () => {
    // A GPU box is a rack with accelerators bolted on; a control-plane box is
    // a rack with a mast. Drawn that way, what distinguishes them is the thing
    // that was added.
    const chassis = partsOf('host-cpu')[0]!;
    for (const form of ['host-gpu', 'host-control'] as const) {
      const parts = partsOf(form);
      expect(parts.length).toBe(2);
      expect(parts[0]!.solid).toBe(chassis.solid);
      expect(parts[0]!.scale).toEqual(chassis.scale);
      // And the addition sits on top of it.
      expect(parts[1]!.offset[1]).toBeGreaterThan(parts[0]!.offset[1]);
    }
  });

  it('gives memory no body, because it is drawn as a well', () => {
    expect(partsOf('store')).toEqual([]);
  });

  it('draws nothing as a torus or a drum', () => {
    // Both read as a doughnut the moment the camera comes above them, which is
    // where this camera spends most of its time.
    const solids = new Set(FORMS.flatMap((form) => partsOf(form).map((part) => part.solid)));
    expect(solids.has('gem')).toBe(true);
    expect([...solids].every((solid) => solid !== ('torus' as never))).toBe(true);
  });

  it('keeps every part inside a sensible reach of the node it belongs to', () => {
    // Parts are sized in units of the node's radius; one that ran away would
    // burst out of the region holding it.
    for (const form of FORMS) {
      for (const part of partsOf(form)) {
        for (const axis of [0, 1, 2] as const) {
          expect(Math.abs(part.offset[axis]) + part.scale[axis]).toBeLessThanOrEqual(2);
        }
      }
    }
  });
});
