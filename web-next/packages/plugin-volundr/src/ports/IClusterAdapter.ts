import type { Cluster } from '../domain/cluster';
import type { SessionReadOptions } from './IVolundrService';

/** Port for querying cluster state from the k8s API (or a mock/test double). */
export interface IClusterAdapter {
  getClusters(options?: SessionReadOptions): Promise<Cluster[]>;
  getCluster(id: string): Promise<Cluster | null>;
}
