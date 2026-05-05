export interface WorkflowRegistryMount {
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

export const MIMIR_MOUNT_MIME = 'application/niuu-mimir-mount';
export const EPHEMERAL_LOCAL_MOUNT_ID = '__ephemeral_local__';

export const EPHEMERAL_LOCAL_MOUNT: WorkflowRegistryMount = {
  id: EPHEMERAL_LOCAL_MOUNT_ID,
  name: 'Ephemeral Local Mimir',
  kind: 'local',
  lifecycle: 'ephemeral',
  role: 'local',
  url: '',
  path: '',
  categories: ['scratch', 'workspace'],
  authRef: null,
  defaultReadPriority: 10,
  enabled: true,
  healthStatus: 'unknown',
  healthMessage: 'Created inside the session workspace at runtime',
  desc: 'Workspace-local scratch Mimir. Path is assigned automatically at runtime.',
};

export function serializeWorkflowRegistryMount(mount: WorkflowRegistryMount): string {
  return JSON.stringify(mount);
}

export function parseWorkflowRegistryMount(raw: string): WorkflowRegistryMount | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<WorkflowRegistryMount>;
    if (typeof parsed.id !== 'string' || typeof parsed.name !== 'string') {
      return null;
    }
    return {
      id: parsed.id,
      name: parsed.name,
      kind: parsed.kind === 'remote' ? 'remote' : 'local',
      lifecycle: parsed.lifecycle === 'ephemeral' ? 'ephemeral' : 'registered',
      role:
        parsed.role === 'domain' || parsed.role === 'local' || parsed.role === 'shared'
          ? parsed.role
          : 'shared',
      url: typeof parsed.url === 'string' ? parsed.url : '',
      path: typeof parsed.path === 'string' ? parsed.path : '',
      categories: Array.isArray(parsed.categories) ? parsed.categories.filter(Boolean) : [],
      authRef: typeof parsed.authRef === 'string' ? parsed.authRef : null,
      defaultReadPriority:
        typeof parsed.defaultReadPriority === 'number' ? parsed.defaultReadPriority : 10,
      enabled: parsed.enabled !== false,
      healthStatus:
        parsed.healthStatus === 'healthy' ||
        parsed.healthStatus === 'degraded' ||
        parsed.healthStatus === 'down'
          ? parsed.healthStatus
          : 'unknown',
      healthMessage: typeof parsed.healthMessage === 'string' ? parsed.healthMessage : '',
      desc: typeof parsed.desc === 'string' ? parsed.desc : '',
    };
  } catch {
    return null;
  }
}
