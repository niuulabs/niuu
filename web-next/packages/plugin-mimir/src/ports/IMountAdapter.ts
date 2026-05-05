import type { Mount } from '@niuulabs/domain';
import type { WriteRoutingRule } from '../domain/routing';
import type { RavnBinding } from '../domain/ravn-binding';
import type { RegistryMount } from '../domain/registry';

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
  kind: 'write' | 'compile' | 'lint-fix' | 'dream';
  message: string;
}

/**
 * Port: IMountAdapter
 *
 * Provides access to the set of Mímir mounts registered in a deployment,
 * including write-routing rules and ravn mount bindings.
 */
export interface IMountAdapter {
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

  /** List write-routing rules, ordered by ascending priority. */
  listRoutingRules(): Promise<WriteRoutingRule[]>;

  /**
   * Create or update a write-routing rule.
   * Returns the persisted rule (server may assign an id for new rules).
   */
  upsertRoutingRule(rule: WriteRoutingRule): Promise<WriteRoutingRule>;

  /** Delete a routing rule by id. No-op if the id does not exist. */
  deleteRoutingRule(id: string): Promise<void>;

  /** List ravn bindings — each ravn's mount access and last dream summary. */
  listRavnBindings(): Promise<RavnBinding[]>;

  /**
   * Fetch the most recent write events across all mounts, newest first.
   * Used by the Overview screen's activity feed.
   */
  getRecentWrites(limit?: number): Promise<RecentWrite[]>;
}
