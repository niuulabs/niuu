export interface RegistryMount {
  id: string;
  name: string;
  kind: 'local' | 'remote';
  lifecycle: 'registered' | 'ephemeral';
  role: 'local' | 'shared' | 'domain';
  url: string;
  path: string;
  categories: string[] | null;
  authRef?: string | null;
  defaultReadPriority: number;
  enabled: boolean;
  healthStatus: 'healthy' | 'degraded' | 'down' | 'unknown';
  healthMessage: string;
  desc: string;
}
