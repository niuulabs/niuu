import { useState, useEffect, useCallback } from 'react';
import { createApiClient } from '@/modules/shared/api/client';
import type { PersonaConfig } from './useFlockConfig';

export type { PersonaConfig };

const api = createApiClient('/api/v1/tyr/dispatch');
const volundrApi = createApiClient('/api/v1/volundr');

export interface QueueItem {
  saga_id: string;
  saga_name: string;
  saga_slug: string;
  repos: string[];
  feature_branch: string;
  phase_name: string;
  issue_id: string;
  identifier: string;
  title: string;
  description: string;
  status: string;
  priority: number;
  priority_label: string;
  estimate: number | null;
  url: string;
}

export interface ModelOption {
  id: string;
  name: string;
  provider?: string;
}

export interface SessionDefinitionOption {
  key: string;
  display_name: string;
  description: string;
  labels: string[];
  default_model: string;
  compatible_providers: string[];
}

export interface DispatchDefaults {
  default_system_prompt: string;
  default_model: string;
  models: ModelOption[];
  session_definitions: SessionDefinitionOption[];
  flock_enabled: boolean;
  flock_default_personas: PersonaConfig[];
  flock_llm_config: Record<string, unknown>;
  flock_sleipnir_publish_urls: string[];
}

export interface ClusterInfo {
  connection_id: string;
  name: string;
  url: string;
  enabled: boolean;
}

interface DispatchItem {
  saga_id: string;
  issue_id: string;
  repo: string;
  connection_id?: string;
}

interface DispatchResult {
  issue_id: string;
  session_id: string;
  session_name: string;
  status: string;
  cluster_name: string;
}

interface UseDispatchQueueResult {
  queue: QueueItem[];
  defaults: DispatchDefaults;
  clusters: ClusterInfo[];
  loading: boolean;
  error: string | null;
  dispatching: boolean;
  refresh: () => void;
  dispatch: (
    items: DispatchItem[],
    model: string,
    systemPrompt: string,
    connectionId?: string,
    workloadType?: string,
    workloadConfig?: Record<string, unknown>,
    sessionDefinition?: string
  ) => Promise<DispatchResult[]>;
}

export function useDispatchQueue(): UseDispatchQueueResult {
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [defaults, setDefaults] = useState<DispatchDefaults>({
    default_system_prompt: '',
    default_model: 'claude-sonnet-4-6',
    models: [],
    session_definitions: [],
    flock_enabled: false,
    flock_default_personas: [],
    flock_llm_config: {},
    flock_sleipnir_publish_urls: [],
  });
  const [clusters, setClusters] = useState<ClusterInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dispatching, setDispatching] = useState(false);

  const fetchQueue = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [queueData, configData, clusterData, sessionDefinitions] = await Promise.all([
        api.get<QueueItem[]>('/queue'),
        api.get<Omit<DispatchDefaults, 'session_definitions'>>('/config'),
        api.get<ClusterInfo[]>('/clusters'),
        volundrApi
          .get<SessionDefinitionOption[]>('/session-definitions')
          .catch(() => [] as SessionDefinitionOption[]),
      ]);
      setQueue(queueData);
      setDefaults({ ...configData, session_definitions: sessionDefinitions });
      setClusters(clusterData);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchQueue();
  }, [fetchQueue]);

  const dispatch = useCallback(
    async (
      items: DispatchItem[],
      model: string,
      systemPrompt: string,
      connectionId?: string,
      workloadType?: string,
      workloadConfig?: Record<string, unknown>,
      sessionDefinition?: string
    ): Promise<DispatchResult[]> => {
      setDispatching(true);
      try {
        const results = await api.post<DispatchResult[]>('/approve', {
          items,
          model,
          system_prompt: systemPrompt,
          ...(connectionId ? { connection_id: connectionId } : {}),
          ...(workloadType ? { workload_type: workloadType } : {}),
          ...(workloadConfig ? { workload_config: workloadConfig } : {}),
          ...(sessionDefinition ? { session_definition: sessionDefinition } : {}),
        });
        // Remove dispatched items from queue locally
        const dispatched = new Set(
          results.filter(r => r.status === 'spawned').map(r => r.issue_id)
        );
        setQueue(prev => prev.filter(q => !dispatched.has(q.issue_id)));
        return results;
      } finally {
        setDispatching(false);
      }
    },
    []
  );

  return { queue, defaults, clusters, loading, error, dispatching, refresh: fetchQueue, dispatch };
}
