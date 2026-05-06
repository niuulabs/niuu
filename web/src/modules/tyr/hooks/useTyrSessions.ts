import { useState, useEffect, useCallback } from 'react';
import { createApiClient } from '@/modules/shared/api/client';

const volundrApi = createApiClient('/api/v1/volundr');

export interface VolundrSession {
  id: string;
  name: string;
  model: string;
  source: {
    repo: string;
    branch: string;
  };
  status: string;
  chat_endpoint: string | null;
  code_endpoint: string | null;
  created_at: string;
  updated_at: string;
  last_active: string;
  message_count: number;
  tokens_used: number;
  tracker_issue_id: string | null;
  issue_tracker_url: string | null;
  error: string | null;
  workload_type: string | null;
  mesh_participants: string[] | null;
}

export interface TimelineEvent {
  t: number;
  type: string;
  label: string;
  tokens?: number | null;
  action?: string | null;
  ins?: number | null;
  del?: number | null;
  hash?: string | null;
  exit?: number | null;
}

export interface TimelineFile {
  path: string;
  status: string;
  ins: number;
  del: number;
}

export interface TimelineCommit {
  hash: string;
  msg: string;
  time: string;
}

export interface TimelineResponse {
  events: TimelineEvent[];
  files: TimelineFile[];
  commits: TimelineCommit[];
  token_burn: number[];
}

interface UseTyrSessionsResult {
  sessions: VolundrSession[];
  loading: boolean;
  error: string | null;
  refresh: () => void;
  getTimeline: (sessionId: string) => Promise<TimelineResponse | null>;
}

export function useTyrSessions(): UseTyrSessionsResult {
  const [sessions, setSessions] = useState<VolundrSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchSessions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await volundrApi.get<VolundrSession[]>('/sessions');
      setSessions(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  const getTimeline = useCallback(async (sessionId: string): Promise<TimelineResponse | null> => {
    try {
      return await volundrApi.get<TimelineResponse>(`/chronicles/${sessionId}/timeline`);
    } catch {
      return null;
    }
  }, []);

  return { sessions, loading, error, refresh: fetchSessions, getTimeline };
}
