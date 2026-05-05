/** Quota domain — per-entity session and resource limits within a cluster. */

export interface QuotaLimit {
  maxConcurrentSessions: number;
  maxCpuCores: number;
  maxMemMi: number;
  maxGpu: number;
}

export type QuotaScope = 'raven' | 'persona' | 'tenant';

/** Quota for a specific entity (raven, persona, or tenant) on a given cluster. */
export interface Quota {
  clusterId: string;
  scope: QuotaScope;
  entityId: string;
  limit: QuotaLimit;
  used: QuotaLimit;
}
