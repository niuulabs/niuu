export interface RegistryMount {
  id: string;
  name: string;
  kind: 'local' | 'remote';
  lifecycle: 'registered' | 'ephemeral';
  role: 'local' | 'shared' | 'domain';
  url: string;
  path: string;
  categories: string[] | null;
  adapter?: string;
  kwargs?: Record<string, unknown>;
  secretKwargsEnv?: Record<string, string>;
  authRef?: string | null;
  defaultReadPriority: number;
  enabled: boolean;
  healthStatus: 'healthy' | 'degraded' | 'down' | 'unknown';
  healthMessage: string;
  desc: string;
}
