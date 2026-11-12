import type {
  DeploymentInspectionResult,
  InstanceInspection,
  KnowledgeDeployment,
  DeploymentStatus,
} from '../domain/instances';
import type { Mount } from '@niuulabs/domain';
import type { RegistryMount } from '../domain/registry';
import type { EvalReport, QueryStats } from '../domain/analytics';

/**
 * A single entry in the recent-writes activity feed.
 * Displayed on the Overview screen in reverse-chronological order.
 */
export interface RecentWrite {
  id: string;
  /** ISO-8601 timestamp. */
  timestamp: string;
  mount: string;
  /** Page path that was written, or empty string for non-page events. */
  page: string;
  /** Ravn that performed the write. */
  ravn: string;
  kind: 'write' | 'compile';
  message: string;
}

/**
 * Port: IMountAdapter
 *
 * Provides access to the set of Mímir mounts registered in a deployment.
 */
export interface IMountAdapter {
  inspectInstances?(mount?: string): Promise<InstanceInspection[]>;
  inspectDeployment?(name: string, target?: string): Promise<DeploymentInspectionResult>;
  controlDeployment?(
    name: string,
    action: 'start' | 'stop' | 'delete' | 'update',
    target?: string,
  ): Promise<unknown>;
  getDeployments?(): Promise<DeploymentStatus>;
  deployInstance?(request: KnowledgeDeployment): Promise<unknown>;

  /** List all registered mounts and their current status. */
  listMounts(): Promise<Mount[]>;

  /** List known registry entries, including inactive ones. */
  listRegistryMounts?(): Promise<RegistryMount[]>;

  /** Create a new registry entry. */
  createRegistryMount?(mount: Omit<RegistryMount, 'id'>): Promise<RegistryMount>;

  /** Update an existing registry entry. */
  updateRegistryMount?(id: string, mount: Omit<RegistryMount, 'id'>): Promise<RegistryMount>;

  /** Delete a registry entry. */
  deleteRegistryMount?(id: string): Promise<void>;

  /**
   * Fetch the most recent write events across all mounts, newest first.
   * Used by the Overview screen's activity feed.
   */
  getRecentWrites(limit?: number): Promise<RecentWrite[]>;

  /**
   * Fetch the latest retrieval-eval benchmark report.
   * Returns null when the backend has no eval subsystem (or no report yet).
   */
  getEvalReport(): Promise<EvalReport | null>;

  /**
   * Fetch live query-traffic statistics.
   * Returns null when the backend does not track query stats.
   */
  getQueryStats(): Promise<QueryStats | null>;
}
