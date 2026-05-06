import { useEffect, useState } from 'react';
import { MOCK_SESSIONS } from '../api/mockData';
import styles from './SessionsView.module.css';

interface AgentSession {
  id: string;
  status: string;
  model: string;
  created_at: string;
  persona?: string;
}

async function fetchSessions(): Promise<AgentSession[]> {
  const resp = await fetch('/api/v1/ravn/sessions');
  if (!resp.ok) return [];
  return resp.json();
}

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60_000);

  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;

  const diffHrs = Math.floor(diffMin / 60);
  if (diffHrs < 24) return `${diffHrs}h ago`;

  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function SessionsView() {
  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [usingMock, setUsingMock] = useState(false);

  useEffect(() => {
    fetchSessions()
      .then(data => {
        if (data.length > 0) {
          setSessions(data);
          setUsingMock(false);
        } else {
          setSessions(MOCK_SESSIONS);
          setUsingMock(true);
        }
      })
      .catch(() => {
        setSessions(MOCK_SESSIONS);
        setUsingMock(true);
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className={styles.empty}>Loading agent sessions…</div>;
  }

  return (
    <div className={styles.container}>
      <h2 className={styles.heading}>Active Agent Sessions</h2>

      {usingMock && <div className={styles.demoBanner}>Demo data — backend not connected</div>}

      {sessions.length === 0 ? (
        <p className={styles.empty}>No active agent sessions.</p>
      ) : (
        <ul className={styles.list}>
          {sessions.map(session => (
            <li key={session.id} className={styles.item}>
              <span className={styles.sessionId}>{session.id.slice(0, 8)}</span>
              <span className={styles[`status_${session.status}`] ?? styles.statusDefault}>
                {session.status}
              </span>
              {session.persona && <span className={styles.persona}>{session.persona}</span>}
              <span className={styles.meta}>{session.model}</span>
              <span className={styles.meta}>{formatTimestamp(session.created_at)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
