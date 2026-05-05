/** Pod domain — image, mounts, resources, and tool configuration for a dev pod. */

export type MountKind = 'git' | 'pvc' | 'secret' | 'configmap';

export interface GitMountSource {
  kind: 'git';
  repo: string;
  branch: string;
}

export interface PvcMountSource {
  kind: 'pvc';
  name: string;
}

export interface SecretMountSource {
  kind: 'secret';
  name: string;
}

export interface ConfigMapMountSource {
  kind: 'configmap';
  name: string;
}

export type MountSource =
  | GitMountSource
  | PvcMountSource
  | SecretMountSource
  | ConfigMapMountSource;

export interface Mount {
  name: string;
  mountPath: string;
  source: MountSource;
  readOnly: boolean;
}

export interface ResourceSpec {
  cpuRequest: string;
  cpuLimit: string;
  memRequestMi: number;
  memLimitMi: number;
  gpuCount: number;
}

/** MCP server definition — one entry per server exposed to the agent in a pod. */
export interface McpServer {
  name: string;
  /** Transport protocol: stdio | http | sse. */
  transport: string;
  /** Connection string — command for stdio, URL for http/sse. */
  connectionString: string;
  /** Tool names exposed by this server. */
  tools: string[];
}

/** Full specification for a pod — runtime config + resource bounds. */
export interface PodSpec {
  image: string;
  tag: string;
  mounts: Mount[];
  env: Record<string, string>;
  envSecretRefs: string[];
  /** Tool IDs from Ravn's registry that this pod exposes to its bound raven. */
  tools: string[];
  /** MCP servers available to the agent in this pod. */
  mcpServers?: McpServer[];
  resources: ResourceSpec;
  /** Maximum session duration in seconds. */
  ttlSec: number;
  /** Auto-terminate after this many seconds of inactivity. */
  idleTimeoutSec: number;
  clusterAffinity?: string[];
  tolerations?: string[];
}
