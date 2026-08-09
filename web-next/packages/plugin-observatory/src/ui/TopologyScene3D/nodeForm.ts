/**
 * What a thing is built out of, on the 3D stage.
 *
 * The plan has one mark per entity type and that is the right answer on paper:
 * a drawing you look at flat has no room for anything else. A model you walk
 * around does, and the questions an operator brings to it are coarser and more
 * physical than "which of twenty types is this" — is that a machine or an
 * agent, does that machine have GPUs in it, is that agent talking to anyone.
 *
 * So a form is not a type. Several types share one, and one type — a host —
 * splits into three, because "has GPUs" is the fact somebody is actually
 * looking for and it is buried if every machine is the same slab.
 */

import type { TopologyNode } from '../../domain';
import { hostRole } from '../../domain/hostRole';

export type NodeForm =
  /** A resident: the most intricate body on the stage, because it thinks. */
  | 'agent'
  /** A machine with GPUs — a rack with a stack of accelerators on it. */
  | 'host-gpu'
  /** A machine that schedules: a rack with a mast. */
  | 'host-control'
  /** A machine that just works: a bare rack. */
  | 'host-cpu'
  /** A workflow session: a spindle, pointed at both ends. */
  | 'session'
  /** A model: a wafer, lying flat the way the plan draws it. */
  | 'model'
  /** A platform service: a pillar, standing there holding something up. */
  | 'service'
  /** A step inside a workflow: a wedge. */
  | 'step'
  /** Something out in the world with a plug in it. */
  | 'device'
  /** Memory, drawn as a well and handled apart from the forms. */
  | 'store';

const SERVICES: ReadonlySet<string> = new Set([
  'ting',
  'bifrost',
  'volundr',
  'skuld',
  'service',
  'warden',
  'namespace',
]);

const STEPS: ReadonlySet<string> = new Set([
  'stage',
  'gate',
  'cond',
  'trigger',
  'end',
  'resource',
  'coord',
]);

const DEVICES: ReadonlySet<string> = new Set(['printer', 'vaettir', 'beacon', 'device']);

const AGENTS: ReadonlySet<string> = new Set(['ravn_long', 'ravn_run', 'valkyrie', 'resident']);

/**
 * Which body a node is built from.
 *
 * A host splits on what the cluster declares about it, never on its name —
 * `hostRole` is the same reading the plan uses, so the two views cannot
 * disagree about which machines carry accelerators.
 */
export function nodeFormOf(node: TopologyNode): NodeForm {
  if (node.typeId === 'mimir') return 'store';
  if (AGENTS.has(node.typeId)) return 'agent';
  if (node.typeId === 'run') return 'session';
  if (node.typeId === 'model') return 'model';
  if (DEVICES.has(node.typeId)) return 'device';
  if (STEPS.has(node.typeId)) return 'step';

  if (node.typeId === 'host') {
    switch (hostRole(node)) {
      case 'gpu':
        return 'host-gpu';
      case 'control-plane':
        return 'host-control';
      default:
        return 'host-cpu';
    }
  }

  if (SERVICES.has(node.typeId)) return 'service';
  return 'service';
}

/** One solid a form is assembled from, at unit scale. */
export interface FormPart {
  solid: 'box' | 'gem' | 'spindle' | 'pillar' | 'wafer' | 'wedge';
  /** Offset from the node's centre, in units of its radius. */
  offset: readonly [number, number, number];
  /** Size, in units of its radius. */
  scale: readonly [number, number, number];
}

/**
 * How each form is put together.
 *
 * Assembled from parts rather than modelled, because the differences that
 * matter are additive: a GPU box is a rack with accelerators bolted on, a
 * control-plane box is a rack with a mast. Drawn that way, the family
 * resemblance between three kinds of machine survives, and the thing that
 * distinguishes them is the thing that was added.
 *
 * The solids are chosen to be told apart in silhouette from any angle, and to
 * say something about what the thing is. Nothing here is a torus or a drum:
 * both read as a doughnut the moment the camera comes above them, which is
 * where this camera spends most of its time.
 */
export function partsOf(form: NodeForm): readonly FormPart[] {
  switch (form) {
    case 'agent':
      // A cut gem — the most intricate body on the stage, because it thinks.
      return [{ solid: 'gem', offset: [0, 0, 0], scale: [1, 1, 1] }];

    case 'host-cpu':
      return [{ solid: 'box', offset: [0, 0, 0], scale: [1.5, 0.42, 1.05] }];

    case 'host-gpu':
      return [
        { solid: 'box', offset: [0, -0.22, 0], scale: [1.5, 0.42, 1.05] },
        // The accelerators, sitting proud of the chassis.
        { solid: 'box', offset: [0, 0.34, 0], scale: [1.12, 0.44, 0.74] },
      ];

    case 'host-control':
      return [
        { solid: 'box', offset: [0, -0.22, 0], scale: [1.5, 0.42, 1.05] },
        // The mast: this is the machine the others answer to.
        { solid: 'spindle', offset: [0, 0.62, 0], scale: [0.34, 0.72, 0.34] },
      ];

    case 'session':
      // A spindle: pointed at both ends, because a run starts and finishes.
      return [{ solid: 'spindle', offset: [0, 0, 0], scale: [0.78, 1.45, 0.78] }];

    case 'model':
      // A wafer, lying flat the way the plan draws it.
      return [{ solid: 'wafer', offset: [0, 0, 0], scale: [1, 0.2, 1] }];

    case 'service':
      // A pillar: infrastructure, standing there holding something up.
      return [{ solid: 'pillar', offset: [0, 0, 0], scale: [0.78, 1.25, 0.78] }];

    case 'step':
      return [{ solid: 'wedge', offset: [0, 0, 0], scale: [0.85, 0.9, 0.85] }];

    case 'device':
      return [{ solid: 'box', offset: [0, 0, 0], scale: [0.85, 0.85, 0.85] }];

    case 'store':
    default:
      return [];
  }
}
