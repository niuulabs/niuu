import type { Cluster } from '../domain/cluster';

/** Port for querying cluster state from the k8s API (or a mock/test double). */
export interface IClusterAdapter {
  getClusters(): Promise<Cluster[]>;
  getCluster(id: string): Promise<Cluster | null>;
}
