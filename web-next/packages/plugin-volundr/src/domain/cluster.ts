/** Cluster domain — capacity, utilization, and node health for a Völundr cluster. */

export type NodeStatus = 'ready' | 'notready' | 'cordoned';

export type ClusterKind = 'primary' | 'gpu' | 'edge' | 'local' | 'observ' | 'media';

export type ClusterStatus = 'healthy' | 'warning' | 'error';

export type PodStatus = 'running' | 'idle' | 'pending' | 'failed' | 'succeeded';

export interface ClusterNode {
  id: string;
  status: NodeStatus;
  role: string;
}

export interface ClusterCapacity {
  cpu: number;
  memMi: number;
  gpu: number;
}

export interface ClusterDisk {
  usedGi: number;
  totalGi: number;
  systemGi: number;
  podsGi: number;
  logsGi: number;
}

export interface ClusterPod {
  name: string;
  status: PodStatus;
  startedAt: string;
  cpuUsed: number;
  cpuLimit: number;
  memUsedMi: number;
  memLimitMi: number;
  restarts: number;
}

/** A named cluster (realm-bound) onto which Völundr schedules sessions. */
export interface Cluster {
  id: string;
  realm: string;
  name: string;
  kind: ClusterKind;
  status: ClusterStatus;
  region: string;
  capacity: ClusterCapacity;
  used: ClusterCapacity;
  disk: ClusterDisk;
  nodes: ClusterNode[];
  pods: ClusterPod[];
  runningSessions: number;
  queuedProvisions: number;
}
