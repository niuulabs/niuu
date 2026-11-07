export interface InstanceInspection {
  mount: string;
  backend: string;
  metrics: Record<string, string | number>;
  unavailable: string[];
}
export interface KnowledgeDeployment {
  name: string;
  backend: 'mimir' | 'gbrain';
  secret?: string;
  target?: string;
}
export interface DeploymentStatus {
  source_name?: string;
  cluster: string;
  namespace: string;
  backends: string[];
  target?: string;
  targets?: (Omit<DeploymentStatus, 'targets'> & { id: string; error?: string })[];
  releases: {
    name: string;
    backend: string;
    ready: boolean;
    message: string;
    target?: string;
    access_scope?: 'tenant' | 'global' | 'local' | 'unknown';
    can_delete?: boolean;
    can_update?: boolean;
  }[];
}

export interface DeploymentInspectionResult {
  name: string;
  message: string;
  ready: boolean;
  logs: Record<string, string>;
}
