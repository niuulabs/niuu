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
  warden?: boolean;
  warden_overrides?: {
    model?: string;
    persona?: string;
    dream_cycle_cron_expression?: string;
    source_trigger_poll_interval_seconds?: number;
    staleness_trigger_schedule_hours?: number;
  };
  dream?: { enabled: boolean; schedule: string; phases: string[] };
}
export interface DeploymentStatus {
  source_name?: string;
  cluster: string;
  namespace: string;
  backends: string[];
  target?: string;
  warden_available?: boolean;
  dream_available?: boolean;
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
  warden_id?: string;
  dream?: KnowledgeDeployment['dream'];
  logs: Record<string, string>;
  dream_results?: {
    timestamp: string;
    status: string;
    duration_ms?: number;
    phases: { phase: string; status: string; summary?: string; details?: { reason?: string } }[];
  }[];
}
