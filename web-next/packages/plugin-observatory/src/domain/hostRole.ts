/**
 * What a machine is for, from what the cluster already declares about it.
 *
 * Read from Kubernetes' own node-role labels and its reported GPU capacity,
 * never from the hostname. Most of the estate does name its machines
 * `…-controlplane-…` and `…-gpuworker-…`, but a name is a convention someone
 * can break and a label is the thing the scheduler acts on — and the machines
 * where the two would disagree are exactly the ones worth getting right:
 * valaskjalf's Sparks carry GPUs and no role at all.
 */

import type { TopologyNode } from './index';

/** What a host is for. `unknown` is a real answer, not a missing one. */
export type HostRole = 'control-plane' | 'gpu' | 'worker' | 'unknown';

const CONTROL_PLANE_ROLES: ReadonlySet<string> = new Set(['control-plane', 'master']);

function rolesOf(node: TopologyNode): string[] {
  const raw = (node as unknown as Record<string, unknown>).roles;
  return Array.isArray(raw) ? raw.map((role) => String(role).toLowerCase()) : [];
}

function hasGpu(node: TopologyNode): boolean {
  const record = node as unknown as Record<string, unknown>;
  if (typeof record.gpuCount === 'number' && record.gpuCount > 0) return true;
  return typeof record.gpu === 'string' && record.gpu.trim().length > 0;
}

/**
 * Classify one host.
 *
 * A GPU outranks the worker role because it is the more specific fact and the
 * one an operator is looking for — every GPU node in the estate is also a
 * worker, so reporting `worker` would bury it. Control plane outranks both:
 * a machine that schedules is that first, whatever else is bolted to it.
 */
export function hostRole(node: TopologyNode): HostRole {
  const roles = rolesOf(node);
  if (roles.some((role) => CONTROL_PLANE_ROLES.has(role))) return 'control-plane';
  if (hasGpu(node)) return 'gpu';
  if (roles.includes('worker')) return 'worker';
  return 'unknown';
}
