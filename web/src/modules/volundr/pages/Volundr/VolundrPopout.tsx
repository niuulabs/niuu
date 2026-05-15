import { useState, useEffect, useMemo, useCallback, lazy, Suspense } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import {
  Hammer,
  Terminal,
  Code,
  MessageSquare,
  Zap,
  Server,
  ArrowLeft,
  FolderGit2,
  Globe,
} from 'lucide-react';
import { StatusBadge } from '@/modules/shared';
import { SessionTerminal } from '@/modules/volundr/components/SessionTerminal';
import { SessionChat } from '@/modules/shared/components/SessionChat';
import { SessionStartingIndicator } from '@/modules/volundr/components/molecules/SessionStartingIndicator';
import { useVolundr } from '@/modules/volundr/hooks/useVolundr';
import { useBroadcastChannel } from '@/hooks/useBroadcastChannel';
import { useSessionProbe } from '@/modules/volundr/hooks/useSessionStartingPoll';
import type { VolundrSession } from '@/modules/volundr/models';
import { isSessionBooting } from '@/modules/volundr/models';
import { getSourceLabel, getBranch, isGitSource } from '@/utils/source';
import styles from './VolundrPopout.module.css';

const EditorPanel = lazy(() =>
  import('@/modules/volundr/components/EditorPanel/EditorPanel').then(m => ({
    default: m.EditorPanel,
  }))
);

type TabType = 'terminal' | 'code' | 'chat';

export function VolundrPopout() {
  const [searchParams] = useSearchParams();
  const sessionId = searchParams.get('session');
  const tabType = (searchParams.get('tab') as TabType) || 'terminal';

  const { sessions, models, loading, markSessionRunning } = useVolundr();
  const { subscribe } = useBroadcastChannel('volundr-sync');

  // Try to find the session from the fetched list first; fall back to
  // sessionStorage data stashed by the parent window before the popout opened.
  // This avoids the "session not found" flash while the API fetch completes.
  const session = useMemo(() => {
    const fetched = sessions.find(s => s.id === sessionId);
    if (fetched) {
      return fetched;
    }

    if (!sessionId) {
      return undefined;
    }

    try {
      const stored = sessionStorage.getItem(`volundr-popout-session-${sessionId}`);
      if (stored) {
        return JSON.parse(stored) as VolundrSession;
      }
    } catch {
      // Ignore parse errors
    }

    return undefined;
  }, [sessions, sessionId]);
  const selectedModel = session ? models[session.model] : null;
  const isLocal = selectedModel?.provider === 'local';
  const isManual = session?.origin === 'manual';

  const sessionHost = session?.hostname ?? null;
  const sessionChatEndpoint = session?.chatEndpoint ?? null;
  const conversationEndpoint = session
    ? `/api/v1/volundr/sessions/${encodeURIComponent(session.id)}/conversation`
    : null;
  const isRunning = session?.status === 'running';

  // Track whether the WebSocket has been verified as connectable.
  // Reset when the session changes using the React "adjust state during
  // render" pattern to avoid the cascading-render lint warning.
  const [connectionVerified, setConnectionVerified] = useState(false);
  const [prevSessionId, setPrevSessionId] = useState(session?.id);
  if (prevSessionId !== session?.id) {
    setPrevSessionId(session?.id);
    setConnectionVerified(false);
  }

  // Probe URL for testing whether a session is connectable
  const probeWsUrl = useMemo(() => {
    if (sessionChatEndpoint) return sessionChatEndpoint;
    if (!sessionHost) return null;
    const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${wsProto}//${sessionHost}/session`;
  }, [sessionChatEndpoint, sessionHost]);

  const handleSessionReady = useCallback(() => {
    setConnectionVerified(true);
    if (session && isSessionBooting(session.status)) {
      markSessionRunning(session.id);
    }
  }, [session, markSessionRunning]);

  const isSessionReady = isRunning && (connectionVerified || !sessionHost);

  const terminalWsUrl = useMemo(() => {
    if (!sessionHost || !isSessionReady) {
      return null;
    }
    const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    if (sessionChatEndpoint) {
      try {
        const parsed = new URL(sessionChatEndpoint);
        const prefix = parsed.pathname.replace(/\/(api\/)?session$/, '');
        return `${wsProto}//${parsed.host}${prefix}/terminal/ws`;
      } catch {
        /* fall through */
      }
    }
    return `${wsProto}//${sessionHost}/terminal/ws`;
  }, [sessionHost, sessionChatEndpoint, isSessionReady]);

  const chatWsUrl = useMemo(() => {
    // Dev override: ?ravn_ws=ws://localhost:7477/ws
    const params = new URLSearchParams(window.location.search);
    const ravnWs = params.get('ravn_ws');
    if (ravnWs) return ravnWs;

    if (!sessionHost || !isSessionReady) {
      return null;
    }
    if (sessionChatEndpoint) return sessionChatEndpoint;
    const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${wsProto}//${sessionHost}/session`;
  }, [sessionHost, sessionChatEndpoint, isSessionReady]);

  // Update window title
  useEffect(() => {
    if (session) {
      const tabLabel = tabType === 'terminal' ? 'Terminal' : tabType === 'chat' ? 'Chat' : 'IDE';
      document.title = `${tabLabel} — ${session.name}`;
    }
    return () => {
      document.title = 'Hlidskjalf';
    };
  }, [session, tabType]);

  // Subscribe to cross-window updates
  useEffect(() => {
    return subscribe(msg => {
      if (msg.type === 'SESSION_STOPPED' || msg.type === 'SESSION_UPDATED') {
        // Session updates are handled by useVolundr's own subscription
      }
    });
  }, [subscribe]);

  // Probe the session's WebSocket endpoint until connectivity is verified
  const needsProbe = !!sessionHost && !connectionVerified;
  useSessionProbe({
    url: probeWsUrl,
    enabled: needsProbe,
    onReady: handleSessionReady,
  });

  if (!session) {
    // Still loading from the API — show a loading state instead of an error
    if (loading) {
      return (
        <div className={styles.wrapper}>
          <div className={styles.error}>
            <Hammer className={styles.errorIcon} />
            <p>Loading session...</p>
          </div>
        </div>
      );
    }

    return (
      <div className={styles.wrapper}>
        <div className={styles.error}>
          <Hammer className={styles.errorIcon} />
          <p>Session not found</p>
          <Link to="/volundr" className={styles.backLink}>
            <ArrowLeft className={styles.backIcon} />
            Return to Völundr
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.wrapper}>
      <header className={styles.header}>
        <div className={styles.headerLeft}>
          <div className={styles.iconContainer}>
            {tabType === 'terminal' ? (
              <Terminal className={styles.icon} />
            ) : tabType === 'chat' ? (
              <MessageSquare className={styles.icon} />
            ) : (
              <Code className={styles.icon} />
            )}
          </div>
          <div className={styles.headerInfo}>
            <div className={styles.titleRow}>
              <span className={styles.title}>{session.name}</span>
              <StatusBadge status={session.status} />
            </div>
            <div className={styles.repoInfo}>
              {isManual ? (
                <>
                  <Globe className={styles.repoIcon} />
                  <span>{session.hostname}</span>
                </>
              ) : (
                <>
                  <FolderGit2 className={styles.repoIcon} />
                  <span>{getSourceLabel(session.source)}</span>
                  {isGitSource(session.source) && (
                    <>
                      <span className={styles.branchArrow}>→</span>
                      <span className={styles.branchName}>{getBranch(session.source)}</span>
                    </>
                  )}
                </>
              )}
              {selectedModel && (
                <span
                  className={styles.modelBadge}
                  style={{ '--model-color': selectedModel.color } as React.CSSProperties}
                >
                  {isLocal ? (
                    <Zap className={styles.modelIcon} />
                  ) : (
                    <Server className={styles.modelIcon} />
                  )}
                  {selectedModel.name}
                </span>
              )}
            </div>
          </div>
        </div>
        <Link to="/volundr" className={styles.backLink}>
          <ArrowLeft className={styles.backIcon} />
          Main View
        </Link>
      </header>

      <main className={styles.content}>
        {tabType === 'terminal' ? (
          isSessionReady ? (
            <SessionTerminal url={terminalWsUrl} className={styles.fullPanel} />
          ) : isSessionBooting(session.status) || (isRunning && !connectionVerified) ? (
            <SessionStartingIndicator className={styles.fullPanel} />
          ) : (
            <div className={styles.emptyState}>
              <Terminal className={styles.emptyIcon} />
              <p>Session is not running</p>
            </div>
          )
        ) : tabType === 'chat' ? (
          isSessionBooting(session.status) || (isRunning && !connectionVerified) ? (
            <SessionStartingIndicator className={styles.fullPanel} />
          ) : conversationEndpoint ? (
            <SessionChat
              url={chatWsUrl}
              historyEndpoint={conversationEndpoint}
              className={styles.fullPanel}
              sessionHost={sessionHost}
              chatEndpoint={sessionChatEndpoint}
            />
          ) : (
            <div className={styles.emptyState}>
              <Hammer className={styles.emptyIcon} />
              <p>Session is not running</p>
            </div>
          )
        ) : isSessionReady ? (
          <Suspense fallback={null}>
            <EditorPanel
              hostname={session.hostname ?? null}
              sessionId={session.id}
              codeEndpoint={session.codeEndpoint}
              className={styles.fullPanel}
            />
          </Suspense>
        ) : session.status === 'starting' ||
          session.status === 'provisioning' ||
          (isRunning && !connectionVerified) ? (
          <SessionStartingIndicator className={styles.fullPanel} />
        ) : (
          <div className={styles.emptyState}>
            <Code className={styles.emptyIcon} />
            <p>Session is not running</p>
          </div>
        )}
      </main>
    </div>
  );
}
