import { useState, useMemo, useRef, useEffect } from 'react';
import { useService } from '@niuulabs/plugin-sdk';
import {
  LifecycleBadge,
  LoadingState,
  ErrorState,
  cn,
  Meter,
  MeshEventCard,
  resolveParticipantColor,
} from '@niuulabs/ui';
import type { MeshEvent, MeshEventType, RoomParticipant } from '@niuulabs/ui';
import { SourceLabel } from './atoms/SourceLabel';
import { ClusterChip } from './atoms/ClusterChip';
import { Terminal } from './Terminal/Terminal';
import { SessionFilesWorkspace } from './SessionFilesWorkspace';
import { useSessionDetail } from './hooks/useSessionStore';
import { toLifecycleState } from './utils/toLifecycleState';
import { buildMockRoom, buildMockTurns, groupTurns } from '../testing/mockChatData';
import type { ChatTurn, PeerMeta, MockRoom, TurnGroup } from '../testing/mockChatData';
import type { IPtyStream } from '../ports/IPtyStream';
import type { IFileSystemPort } from '../ports/IFileSystemPort';
import type { Session, SessionFileStats } from '../domain/session';
import type { SessionSource } from './atoms/SourceLabel';
import type { ClusterData } from './atoms/ClusterChip';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type SessionTab = 'chat' | 'terminal' | 'diffs' | 'files' | 'chronicle' | 'logs';

const TABS: { id: SessionTab; label: string }[] = [
  { id: 'chat', label: 'Chat' },
  { id: 'terminal', label: 'Terminal' },
  { id: 'diffs', label: 'Diffs' },
  { id: 'files', label: 'Files' },
  { id: 'chronicle', label: 'Chronicle' },
  { id: 'logs', label: 'Logs' },
];

function tabIcon(id: SessionTab): string {
  switch (id) {
    case 'chat':
      return '\u25AD';
    case 'terminal':
      return '>_';
    case 'diffs':
      return '\u296E';
    case 'files':
      return '\u25F0';
    case 'chronicle':
      return '\u2263';
    case 'logs':
      return '\u2328';
  }
}

export interface SessionDetailPageProps {
  sessionId: string;
  readOnly?: boolean;
  initialTab?: SessionTab;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function truncate(s: string | undefined, n: number): string {
  if (!s) return '';
  return s.length > n ? s.slice(0, n - 1) + '\u2026' : s;
}

function formatTimestamp(ts: number): string {
  const d = new Date(ts);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function tabCount(tab: SessionTab, session: Session): number | undefined {
  switch (tab) {
    case 'chat':
      return session.events.length;
    case 'diffs':
      return (
        (session.files?.added ?? 0) + (session.files?.modified ?? 0) + (session.files?.deleted ?? 0)
      );
    case 'chronicle':
      return session.events.length;
    default:
      return undefined;
  }
}

// ---------------------------------------------------------------------------
// Stat helper
// ---------------------------------------------------------------------------

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="niuu-flex niuu-flex-col niuu-items-center niuu-gap-0.5" data-testid="stat">
      <span className="niuu-text-[10px] niuu-uppercase niuu-tracking-wider niuu-text-text-muted">
        {label}
      </span>
      <span className="niuu-font-mono niuu-text-xs niuu-text-text-primary">{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// FileChangeSummary
// ---------------------------------------------------------------------------

function FileChangeSummary({ files }: { files: SessionFileStats }) {
  return (
    <div
      className="niuu-flex niuu-items-center niuu-gap-3 niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-4 niuu-py-1.5"
      data-testid="file-change-summary"
    >
      <span className="niuu-font-mono niuu-text-[10px] niuu-text-text-muted">files</span>
      <span className="niuu-font-mono niuu-text-xs niuu-text-state-ok" data-testid="files-added">
        +{files.added}
      </span>
      <span
        className="niuu-font-mono niuu-text-xs niuu-text-state-warn"
        data-testid="files-modified"
      >
        ~{files.modified}
      </span>
      <span className="niuu-font-mono niuu-text-xs niuu-text-critical" data-testid="files-deleted">
        -{files.deleted}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SessionHeader
// ---------------------------------------------------------------------------

function SessionHeader({
  session,
  readOnly,
  showRes,
}: {
  session: Session;
  readOnly: boolean;
  showRes: boolean;
}) {
  const r = session.resources;

  // Derive a mock source and cluster from session data
  const source: SessionSource = {
    type: 'git',
    repo: `niuulabs/${session.templateId}`,
    branch: 'main',
  };

  const cluster: ClusterData = {
    name: session.clusterId,
    kind: 'primary',
  };

  // Duration since startedAt
  const elapsedMs = Date.now() - new Date(session.startedAt).getTime();
  const durationMin = Math.floor(elapsedMs / 60_000);
  const duration =
    durationMin >= 60 ? `${Math.floor(durationMin / 60)}h ${durationMin % 60}m` : `${durationMin}m`;

  return (
    <header data-testid="session-header">
      {/* Main row */}
      <div className="niuu-flex niuu-items-center niuu-gap-3 niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-4 niuu-py-2.5">
        <LifecycleBadge
          state={toLifecycleState(session.state)}
          className="!niuu-px-2 !niuu-py-0.5 !niuu-text-[10px] !niuu-leading-none"
        />
        <div className="niuu-flex niuu-items-baseline niuu-gap-4">
          <h1
            className="niuu-font-mono niuu-text-[16px] niuu-font-medium niuu-text-text-primary"
            data-testid="session-id-display"
          >
            {session.id}
          </h1>
          <span
            className="niuu-font-mono niuu-text-[10px] niuu-text-text-muted"
            data-testid="session-name"
          >
            {session.personaName}
          </span>
          <span
            className="niuu-font-mono niuu-text-[10px] niuu-text-text-muted"
            data-testid="session-id-label"
          >
            {session.id}
          </span>
        </div>

        {session.sagaId && (
          <a
            className="niuu-rounded-md niuu-bg-brand-subtle niuu-px-2 niuu-py-0.5 niuu-font-mono niuu-text-[11px] niuu-text-brand hover:niuu-underline"
            href="#"
            data-testid="session-issue-link"
          >
            {session.sagaId}
          </a>
        )}

        <span className="niuu-mx-1 niuu-h-3 niuu-w-px niuu-bg-border-subtle" aria-hidden />

        <div className="niuu-max-w-[320px] niuu-truncate">
          <SourceLabel source={source} short />
        </div>

        <span className="niuu-mx-1 niuu-h-3 niuu-w-px niuu-bg-border-subtle" aria-hidden />

        <div className="niuu-flex niuu-items-center niuu-gap-2">
          <ClusterChip cluster={cluster} />
          <span className="niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.12em] niuu-text-text-muted">
            primary
          </span>
        </div>

        {readOnly && (
          <span
            className="niuu-rounded niuu-bg-bg-elevated niuu-px-2 niuu-py-0.5 niuu-text-xs niuu-text-text-muted"
            data-testid="session-archived-badge"
          >
            archived
          </span>
        )}

        <div className="niuu-flex-1" />

        <div className="niuu-flex niuu-items-center niuu-gap-6" data-testid="session-stats">
          <Stat label="uptime" value={duration} />
          <Stat label="msgs" value={session.events.length} />
          <Stat
            label="tokens"
            value={formatTokens((session.tokensIn ?? 0) + (session.tokensOut ?? 0))}
          />
          <Stat label="cost" value={`$${((session.costCents ?? 0) / 100).toFixed(2)}`} />
        </div>
      </div>

      {/* File change summary row */}
      {session.files && <FileChangeSummary files={session.files} />}

      {/* Resources row (collapsible) */}
      {showRes && (
        <div
          className="niuu-flex niuu-items-center niuu-gap-4 niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-5 niuu-py-2.5"
          data-testid="resources-row"
        >
          <Meter used={r.cpuUsed} limit={r.cpuLimit} unit="c" label="cpu" className="niuu-w-32" />
          <Meter
            used={r.memUsedMi}
            limit={r.memLimitMi}
            unit="Mi"
            label="mem"
            className="niuu-w-32"
          />
          {r.diskUsedMi !== undefined && r.diskLimitMi !== undefined && (
            <Meter
              used={r.diskUsedMi}
              limit={r.diskLimitMi}
              unit="Mi"
              label="disk"
              className="niuu-w-32"
            />
          )}
          {r.gpuCount > 0 && (
            <span className="niuu-font-mono niuu-text-xs niuu-text-text-muted">
              gpu: {r.gpuCount}
            </span>
          )}
        </div>
      )}
    </header>
  );
}

// ---------------------------------------------------------------------------
// ChatStream (center column of chat)
// ---------------------------------------------------------------------------

function ThinkingBlock({ turn, peer }: { turn: ChatTurn; peer: PeerMeta | undefined }) {
  const [open, setOpen] = useState(false);
  const firstLine = (turn.content || '').split('\n')[0] ?? '';
  const color = resolveParticipantColor(peer?.peerId ?? '');

  return (
    <div
      className="niuu-my-2 niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-secondary/70 niuu-px-4 niuu-py-3"
      data-testid="thinking-block"
    >
      <button
        className="niuu-flex niuu-items-center niuu-gap-2 niuu-text-left niuu-text-xs"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="niuu-text-text-muted">{open ? '\u25BC' : '\u25B6'}</span>
        {peer && (
          <span className="niuu-font-mono" style={{ color }}>
            {peer.glyph}
          </span>
        )}
        <span className="niuu-font-mono niuu-uppercase niuu-tracking-[0.16em] niuu-text-text-muted">
          thinking
        </span>
        <span className="niuu-font-mono niuu-text-text-muted">{turn.ms}ms</span>
        {!open && <span className="niuu-text-text-muted">{truncate(firstLine, 80)}</span>}
      </button>
      {open && (
        <pre className="niuu-ml-8 niuu-mt-2 niuu-whitespace-pre-wrap niuu-font-mono niuu-text-xs niuu-leading-6 niuu-text-text-secondary">
          {turn.content}
        </pre>
      )}
    </div>
  );
}

function ToolRunBlock({ turns, room }: { turns: ChatTurn[]; room: MockRoom }) {
  const [open, setOpen] = useState(false);
  const peer = room.byId[turns[0]?.peerId ?? ''];
  const color = resolveParticipantColor(peer?.peerId ?? '');
  const errCount = turns.filter((t) => t.status === 'err').length;
  const okCount = turns.filter((t) => t.status === 'ok').length;
  const headline = turns[turns.length - 1]!;

  return (
    <div
      className="niuu-my-2 niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-secondary"
      data-testid="tool-run"
    >
      <button
        className="niuu-flex niuu-w-full niuu-items-center niuu-gap-2 niuu-px-4 niuu-py-3 niuu-text-left niuu-text-xs"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="niuu-text-text-muted">{open ? '\u25BC' : '\u25B6'}</span>
        {peer && (
          <span className="niuu-font-mono" style={{ color }}>
            {peer.glyph} {peer.displayName}
          </span>
        )}
        <span className="niuu-font-mono niuu-text-text-muted">
          {turns.length} {turns.length === 1 ? 'call' : 'calls'}
        </span>
        <span className="niuu-font-mono niuu-text-text-secondary">{headline.tool}</span>
        <span className="niuu-font-mono niuu-text-text-muted">{truncate(headline.args, 40)}</span>
        <div className="niuu-flex-1" />
        {okCount > 0 && (
          <span className="niuu-font-mono niuu-text-[10px] niuu-text-state-ok">{okCount} ok</span>
        )}
        {errCount > 0 && (
          <span className="niuu-font-mono niuu-text-[10px] niuu-text-critical">{errCount} err</span>
        )}
      </button>
      {open && (
        <div className="niuu-border-t niuu-border-border-subtle niuu-px-4 niuu-py-3">
          {turns.map((t) => (
            <div
              key={t.id}
              className="niuu-flex niuu-items-start niuu-gap-2 niuu-py-1.5 niuu-text-xs"
            >
              <span className="niuu-font-mono niuu-text-text-secondary">{t.tool}</span>
              <span className="niuu-font-mono niuu-text-text-muted">{t.args}</span>
              {t.status === 'ok' && <span className="niuu-font-mono niuu-text-state-ok">ok</span>}
              {t.status === 'err' && <span className="niuu-font-mono niuu-text-critical">err</span>}
              <span className="niuu-font-mono niuu-text-text-muted">{t.dur}</span>
              {t.output && (
                <pre className="niuu-mt-0.5 niuu-whitespace-pre-wrap niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
                  {truncate(t.output, 120)}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ChatTurnComponent({ turn, room }: { turn: ChatTurn; room: MockRoom }) {
  const peer = room.byId[turn.peerId];
  const color = resolveParticipantColor(peer?.peerId ?? '');

  if (turn.role === 'user') {
    return (
      <div className="niuu-my-4 niuu-flex niuu-justify-end niuu-gap-3" data-testid="chat-turn-user">
        <span className="niuu-order-2 niuu-font-mono niuu-text-xs niuu-text-text-muted">you</span>
        <div className="niuu-flex-1">
          {turn.directedTo && turn.directedTo.length > 0 && (
            <div className="niuu-mb-2 niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.16em] niuu-text-text-muted">
              directed {'\u2192'}{' '}
              {turn.directedTo.map((id) => (
                <span
                  key={id}
                  className="niuu-ml-1 niuu-rounded-md niuu-border niuu-border-brand/40 niuu-bg-brand/10 niuu-px-1.5 niuu-py-0.5"
                  style={{ color: resolveParticipantColor(id) }}
                >
                  {room.byId[id]?.displayName ?? id}
                </span>
              ))}
            </div>
          )}
          <div className="niuu-ml-auto niuu-max-w-[82%] niuu-rounded-xl niuu-border niuu-border-brand/30 niuu-bg-bg-secondary niuu-px-4 niuu-py-3 niuu-text-sm niuu-leading-7 niuu-text-text-primary">
            {turn.content}
          </div>
        </div>
        <span className="niuu-order-3 niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
          {formatTimestamp(turn.ts)}
        </span>
      </div>
    );
  }

  // assistant turn
  return (
    <div className="niuu-my-4 niuu-flex niuu-gap-4" data-testid="chat-turn-assistant">
      <span
        className="niuu-flex niuu-h-8 niuu-w-8 niuu-flex-shrink-0 niuu-items-center niuu-justify-center niuu-rounded-full niuu-border niuu-font-mono niuu-text-xs"
        style={{ borderColor: color, color }}
      >
        {peer?.glyph ?? 'c'}
      </span>
      <div className="niuu-flex-1">
        <div className="niuu-mb-2 niuu-flex niuu-items-center niuu-gap-2 niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
          <span style={{ color }}>{peer?.displayName ?? turn.peerId}</span>
          <span>{'\u00b7'}</span>
          <span>{peer?.persona ?? ''}</span>
          {turn.tokens && (
            <>
              <span>{'\u00b7'}</span>
              <span>{turn.tokens}t</span>
            </>
          )}
          {turn.ms && (
            <>
              <span>{'\u00b7'}</span>
              <span>{turn.ms}ms</span>
            </>
          )}
        </div>
        <div className="niuu-max-w-[88%] niuu-text-sm niuu-leading-7 niuu-text-text-primary">
          {turn.content}
        </div>
        {turn.outcome && (
          <div
            className="niuu-mt-3 niuu-max-w-[88%] niuu-rounded-xl niuu-border niuu-border-brand/40 niuu-bg-bg-secondary niuu-p-3"
            data-testid="outcome-card"
          >
            <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-text-xs">
              <span className="niuu-font-mono niuu-text-text-muted">---outcome---</span>
              <span
                className={cn(
                  'niuu-font-mono niuu-font-semibold niuu-text-xs niuu-uppercase niuu-px-2 niuu-py-0.5 niuu-rounded-md',
                  turn.outcome.verdict === 'pass' || turn.outcome.verdict === 'verified'
                    ? 'niuu-text-state-ok niuu-bg-state-ok/10'
                    : turn.outcome.verdict === 'fail' || turn.outcome.verdict === 'blocked'
                      ? 'niuu-text-critical niuu-bg-critical-bg'
                      : 'niuu-text-state-warn niuu-bg-state-warn/10',
                )}
              >
                {turn.outcome.verdict}
              </span>
              <span className="niuu-font-mono niuu-text-text-muted">{turn.outcome.eventType}</span>
            </div>
            <div className="niuu-mt-2 niuu-text-xs niuu-leading-6 niuu-text-text-secondary">
              {turn.outcome.summary}
            </div>
          </div>
        )}
      </div>
      <span className="niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
        {formatTimestamp(turn.ts)}
      </span>
    </div>
  );
}

function ChatInput({ participants }: { participants: RoomParticipant[] }) {
  const [value, setValue] = useState('');
  const [permission, setPermission] = useState<'restricted' | 'open'>('restricted');

  return (
    <div className="niuu-border-t niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-4 niuu-py-3 niuu-shrink-0">
      {/* Direct-to chips */}
      <div className="niuu-flex niuu-items-center niuu-gap-1.5 niuu-mb-2 niuu-text-[10px] niuu-text-text-muted">
        <span className="niuu-uppercase niuu-tracking-wider">direct to</span>
        {participants
          .filter((p) => p.participantType === 'ravn')
          .map((p) => (
            <span
              key={p.peerId}
              className="niuu-flex niuu-items-center niuu-gap-1 niuu-rounded-md niuu-bg-bg-tertiary niuu-px-2 niuu-py-0.5 niuu-font-mono niuu-text-text-secondary"
            >
              <span className="niuu-w-1.5 niuu-h-1.5 niuu-rounded-full niuu-bg-brand" />
              {p.displayName ?? p.persona}
            </span>
          ))}
        <span className="niuu-text-text-muted">(broadcast · all participants receive)</span>
      </div>
      {/* Input row */}
      <div className="niuu-flex niuu-items-end niuu-gap-2">
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="broadcast to room...  (⌘↵ send · / commands · @ mention files)"
          className="niuu-flex-1 niuu-bg-bg-tertiary niuu-border niuu-border-border niuu-rounded-xl niuu-px-4 niuu-py-3 niuu-text-sm niuu-text-text-primary niuu-font-sans niuu-resize-y niuu-min-h-[44px] niuu-max-h-[120px] niuu-outline-none focus:niuu-border-brand"
          rows={1}
          data-testid="chat-input"
        />
        <div className="niuu-flex niuu-items-center niuu-gap-2">
          <span className="niuu-text-[10px] niuu-text-text-muted">@</span>
          <span className="niuu-text-[10px] niuu-text-text-muted">/</span>
          <span className="niuu-text-[10px] niuu-text-text-muted">📎</span>
          <span className="niuu-text-xs niuu-text-text-muted">permission:</span>
          <select
            value={permission}
            onChange={(e) => setPermission(e.target.value as 'restricted' | 'open')}
            className="niuu-bg-bg-tertiary niuu-border niuu-border-border niuu-rounded-md niuu-text-xs niuu-text-text-primary niuu-font-mono niuu-py-1.5 niuu-px-2.5"
          >
            <option value="restricted">restricted</option>
            <option value="open">open</option>
          </select>
          <button
            type="button"
            className="niuu-flex niuu-items-center niuu-gap-1 niuu-rounded-md niuu-border niuu-border-brand niuu-bg-brand niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-xs niuu-text-bg-primary niuu-cursor-pointer"
            data-testid="chat-send-btn"
          >
            send <span className="niuu-text-[10px] niuu-opacity-60">⌘↵</span>
          </button>
        </div>
      </div>
    </div>
  );
}

function ChatStream({ groups, room }: { groups: TurnGroup[]; room: MockRoom }) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [groups.length]);

  return (
    <div
      className="niuu-flex niuu-flex-1 niuu-flex-col niuu-overflow-hidden niuu-border-x niuu-border-border-subtle"
      data-testid="chat-stream"
    >
      <div className="niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary/80 niuu-px-4 niuu-py-2">
        <div className="niuu-flex niuu-items-center niuu-gap-3 niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
          <span>sast_findings 0</span>
          <span>deps_changed 0</span>
          <span>
            scope <span className="niuu-text-text-primary">frontend-only</span>
          </span>
        </div>
      </div>
      <div className="niuu-flex-1 niuu-overflow-y-auto niuu-px-4 niuu-py-3" ref={scrollRef}>
        {groups.map((g, i) => {
          if (g.kind === 'toolrun') {
            return <ToolRunBlock key={i} turns={g.turns} room={room} />;
          }
          if (g.kind === 'thinking') {
            return <ThinkingBlock key={i} turn={g.turn} peer={room.byId[g.turn.peerId]} />;
          }
          return <ChatTurnComponent key={i} turn={g.turn} room={room} />;
        })}
        {groups.length === 0 && (
          <div className="niuu-py-8 niuu-text-center niuu-font-mono niuu-text-xs niuu-text-text-muted">
            no messages yet
          </div>
        )}
      </div>
      {/* Chat input bar (web2 parity) */}
      <ChatInput participants={room.participants} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// PeerRail
// ---------------------------------------------------------------------------

function PeerRail({
  room,
  selectedPeerId,
  onSelectPeer,
  collapsed,
  onToggleCollapsed,
}: {
  room: MockRoom;
  selectedPeerId: string | null;
  onSelectPeer: (id: string) => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
}) {
  const peers = room.participants.filter((p) => p.participantType === 'ravn');
  if (collapsed) {
    return (
      <aside
        className="niuu-flex niuu-w-[54px] niuu-flex-col niuu-border-r niuu-border-border-subtle niuu-bg-bg-secondary"
        data-testid="mesh-sidebar"
      >
        <button
          className="niuu-py-3 niuu-font-mono niuu-text-sm niuu-text-text-muted"
          onClick={onToggleCollapsed}
        >
          ›
        </button>
        <div className="niuu-flex niuu-flex-col niuu-items-center niuu-gap-3 niuu-py-3">
          {peers.map((peer) => (
            <button
              key={peer.peerId}
              onClick={() => onSelectPeer(peer.peerId)}
              className={cn(
                'niuu-flex niuu-h-8 niuu-w-8 niuu-items-center niuu-justify-center niuu-rounded-full niuu-border niuu-font-mono niuu-text-xs',
                selectedPeerId === peer.peerId
                  ? 'niuu-border-brand niuu-text-brand'
                  : 'niuu-border-border niuu-text-text-muted',
              )}
              title={peer.displayName ?? peer.persona}
            >
              {peer.glyph}
            </button>
          ))}
        </div>
      </aside>
    );
  }

  return (
    <aside
      className="niuu-flex niuu-w-[232px] niuu-flex-col niuu-border-r niuu-border-border-subtle niuu-bg-bg-secondary"
      data-testid="mesh-sidebar"
    >
      <div className="niuu-flex niuu-items-center niuu-justify-between niuu-border-b niuu-border-border-subtle niuu-px-4 niuu-py-3">
        <span className="niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-muted">
          participants {peers.length}
        </span>
        <button
          className="niuu-font-mono niuu-text-lg niuu-text-text-muted"
          onClick={onToggleCollapsed}
        >
          ‹
        </button>
      </div>
      <div className="niuu-flex-1 niuu-overflow-y-auto">
        {peers.map((peer) => (
          <button
            key={peer.peerId}
            onClick={() => onSelectPeer(peer.peerId)}
            className={cn(
              'niuu-flex niuu-w-full niuu-items-center niuu-gap-3 niuu-border-b niuu-border-border-subtle niuu-px-4 niuu-py-3 niuu-text-left',
              selectedPeerId === peer.peerId && 'niuu-chat-peer-card--selected niuu-bg-bg-elevated',
            )}
            data-testid={`peer-card-${peer.peerId}`}
          >
            <span
              className="niuu-flex niuu-h-9 niuu-w-9 niuu-items-center niuu-justify-center niuu-rounded-full niuu-border niuu-font-mono niuu-text-sm"
              style={{
                color: resolveParticipantColor(peer.peerId),
                borderColor: resolveParticipantColor(peer.peerId),
              }}
            >
              {peer.glyph}
            </span>
            <span className="niuu-flex-1 niuu-min-w-0">
              <span className="niuu-block niuu-font-mono niuu-text-[13px] niuu-font-medium niuu-text-text-primary">
                {peer.displayName ?? peer.persona}
              </span>
              <span className="niuu-block niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
                {peer.status}
              </span>
            </span>
            <span className="niuu-font-mono niuu-text-xl niuu-text-text-muted">›</span>
          </button>
        ))}
      </div>
      <div className="niuu-border-t niuu-border-border-subtle niuu-px-5 niuu-py-3 niuu-font-mono niuu-text-[12px] niuu-text-text-faint">
        room · {room.roomId}
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// MeshCascade (right column of chat)
// ---------------------------------------------------------------------------

type CascadeFilterType = 'all' | MeshEventType;

function MeshCascade({
  events,
  filter,
  setFilter,
  collapsed,
  onToggleCollapsed,
}: {
  events: MeshEvent[];
  filter: CascadeFilterType;
  setFilter: (f: CascadeFilterType) => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
}) {
  const filtered = filter === 'all' ? events : events.filter((e) => e.type === filter);

  const filterOptions: { id: CascadeFilterType; label: string }[] = [
    { id: 'all', label: 'all' },
    { id: 'outcome', label: 'outcomes' },
    { id: 'mesh_message', label: 'delegations' },
    { id: 'notification', label: 'notifs' },
  ];

  if (collapsed) {
    return (
      <div
        className="niuu-flex niuu-w-[54px] niuu-flex-col niuu-border-l niuu-border-border-subtle niuu-bg-bg-secondary"
        data-testid="mesh-cascade"
      >
        <button
          className="niuu-py-3 niuu-font-mono niuu-text-sm niuu-text-text-muted"
          onClick={onToggleCollapsed}
        >
          ‹
        </button>
      </div>
    );
  }

  return (
    <div
      className="niuu-flex niuu-w-[248px] niuu-flex-col niuu-border-l niuu-border-border-subtle niuu-bg-bg-secondary"
      data-testid="mesh-cascade"
    >
      <div className="niuu-flex niuu-items-center niuu-justify-between niuu-border-b niuu-border-border-subtle niuu-px-4 niuu-py-3">
        <span className="niuu-text-[10px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-muted">
          mesh cascade
        </span>
        <div className="niuu-flex niuu-items-center niuu-gap-3">
          <span className="niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
            {filtered.length}
          </span>
          <button
            className="niuu-font-mono niuu-text-lg niuu-text-text-muted"
            onClick={onToggleCollapsed}
          >
            ›
          </button>
        </div>
      </div>

      <div className="niuu-flex niuu-gap-1 niuu-border-b niuu-border-border-subtle niuu-px-3 niuu-py-2">
        {filterOptions.map((f) => (
          <button
            key={f.id}
            className={cn(
              'niuu-rounded-md niuu-border niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[9px]',
              filter === f.id
                ? 'niuu-border-brand/50 niuu-bg-bg-elevated niuu-text-brand'
                : 'niuu-border-transparent niuu-text-text-muted hover:niuu-border-border-subtle hover:niuu-text-text-secondary',
            )}
            onClick={() => setFilter(f.id)}
            data-testid={`cascade-filter-${f.id}`}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="niuu-flex niuu-flex-1 niuu-flex-col niuu-gap-2 niuu-overflow-y-auto niuu-px-3 niuu-py-3">
        {filtered.map((e) => (
          <div
            key={e.id}
            data-testid="cascade-event"
            className="niuu-overflow-hidden niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-primary"
          >
            <MeshEventCard event={e} />
          </div>
        ))}
        {filtered.length === 0 && (
          <div className="niuu-py-4 niuu-text-center niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
            no events
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ChatTab
// ---------------------------------------------------------------------------

function ChatTab({ session }: { session: Session }) {
  const room = useMemo(() => (import.meta.env.DEV ? buildMockRoom(session) : null), [session]);
  const turns = useMemo(() => (room ? buildMockTurns(session, room) : []), [session, room]);
  const grouped = useMemo(() => groupTurns(turns), [turns]);
  const [focusPeer, setFocusPeer] = useState<string | null>(null);
  const [cascadeFilter, setCascadeFilter] = useState<CascadeFilterType>('all');
  const [peerCollapsed, setPeerCollapsed] = useState(false);
  const [cascadeCollapsed, setCascadeCollapsed] = useState(false);

  const filteredGroups = useMemo(() => {
    if (!focusPeer) return grouped;
    return grouped
      .map((g) => {
        if (g.kind === 'toolrun') {
          const t = g.turns.filter((x) => x.peerId === focusPeer);
          return t.length ? { kind: 'toolrun' as const, turns: t } : null;
        }
        return g.turn.peerId === focusPeer ? g : null;
      })
      .filter((g): g is TurnGroup => g !== null);
  }, [grouped, focusPeer]);

  if (!room) {
    return (
      <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-font-mono niuu-text-sm niuu-text-text-muted">
        Chat {'\u2014'} requires live connection
      </div>
    );
  }

  return (
    <div className="niuu-flex niuu-h-full niuu-bg-bg-primary" data-testid="chat-tab">
      <PeerRail
        room={room}
        selectedPeerId={focusPeer}
        onSelectPeer={(id) => setFocusPeer(focusPeer === id ? null : id)}
        collapsed={peerCollapsed}
        onToggleCollapsed={() => setPeerCollapsed((v) => !v)}
      />
      <ChatStream groups={filteredGroups} room={room} />
      <MeshCascade
        events={room.meshEvents}
        filter={cascadeFilter}
        setFilter={setCascadeFilter}
        collapsed={cascadeCollapsed}
        onToggleCollapsed={() => setCascadeCollapsed((v) => !v)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// DiffsTab
// ---------------------------------------------------------------------------

interface MockDiffFile {
  path: string;
  status: 'new' | 'mod' | 'del';
  ins: number;
  del: number;
}

interface DiffHunkLine {
  type: 'add' | 'remove' | 'ctx';
  content: string;
  oldLine?: number;
  newLine?: number;
}

interface DiffHunk {
  oldStart: number;
  oldCount: number;
  newStart: number;
  newCount: number;
  lines: DiffHunkLine[];
}

const MOCK_DIFF_FILES: MockDiffFile[] = [
  { path: 'src/auth/handler.ts', status: 'mod', ins: 42, del: 8 },
  { path: 'src/auth/jwt.ts', status: 'new', ins: 96, del: 0 },
  { path: 'src/auth/legacy.ts', status: 'del', ins: 0, del: 34 },
];

function buildMockHunks(file: MockDiffFile): DiffHunk[] {
  if (file.status === 'del') {
    return [
      {
        oldStart: 1,
        oldCount: 3,
        newStart: 0,
        newCount: 0,
        lines: [
          { type: 'remove', content: 'export class LegacyAuth {', oldLine: 1 },
          { type: 'remove', content: '  // deprecated — use AuthHandler', oldLine: 2 },
          { type: 'remove', content: '}', oldLine: 3 },
        ],
      },
    ];
  }
  if (file.status === 'new') {
    return [
      {
        oldStart: 0,
        oldCount: 0,
        newStart: 1,
        newCount: 4,
        lines: [
          { type: 'add', content: 'import { verify } from "jsonwebtoken";', newLine: 1 },
          { type: 'add', content: '', newLine: 2 },
          { type: 'add', content: 'export function validateJwt(token: string) {', newLine: 3 },
          { type: 'add', content: '  return verify(token, process.env.JWT_SECRET!);', newLine: 4 },
        ],
      },
    ];
  }
  return [
    {
      oldStart: 10,
      oldCount: 4,
      newStart: 10,
      newCount: 6,
      lines: [
        { type: 'ctx', content: 'export class AuthHandler {', oldLine: 10, newLine: 10 },
        { type: 'remove', content: '  validate(token: string): boolean {', oldLine: 11 },
        {
          type: 'add',
          content: '  async validate(token: string): Promise<boolean> {',
          newLine: 11,
        },
        { type: 'add', content: '    const claims = await validateJwt(token);', newLine: 12 },
        { type: 'ctx', content: '    return !!claims;', oldLine: 12, newLine: 13 },
        { type: 'ctx', content: '  }', oldLine: 13, newLine: 14 },
      ],
    },
  ];
}

function diffStatusLetter(status: MockDiffFile['status']): string {
  return status === 'new' ? 'A' : status === 'mod' ? 'M' : 'D';
}

function diffStatusColor(status: MockDiffFile['status']): string {
  return status === 'new'
    ? 'niuu-text-state-ok'
    : status === 'mod'
      ? 'niuu-text-state-warn'
      : 'niuu-text-critical';
}

function DiffFileList({
  files,
  selectedPath,
  onSelect,
}: {
  files: MockDiffFile[];
  selectedPath: string | null;
  onSelect: (path: string) => void;
}) {
  return (
    <div
      className="niuu-flex niuu-h-full niuu-flex-col niuu-overflow-auto"
      data-testid="diff-file-list"
    >
      <div className="niuu-border-b niuu-border-border-subtle niuu-bg-bg-primary niuu-px-4 niuu-py-3">
        <div className="niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-muted">
          changed files
        </div>
      </div>
      {files.map((f) => (
        <button
          key={f.path}
          onClick={() => onSelect(f.path)}
          className={cn(
            'niuu-flex niuu-items-center niuu-gap-2 niuu-border-b niuu-border-border-subtle niuu-px-4 niuu-py-2.5 niuu-text-left niuu-text-xs hover:niuu-bg-bg-elevated',
            selectedPath === f.path && 'niuu-bg-bg-elevated',
          )}
          data-testid={`diff-file-${f.status}`}
        >
          <span
            className={cn(
              'niuu-w-4 niuu-flex-shrink-0 niuu-font-mono niuu-font-medium',
              diffStatusColor(f.status),
            )}
          >
            {diffStatusLetter(f.status)}
          </span>
          <span className="niuu-min-w-0 niuu-flex-1 niuu-truncate niuu-font-mono niuu-text-text-secondary">
            {f.path}
          </span>
          <span className="niuu-flex-shrink-0 niuu-font-mono niuu-text-[10px]">
            {f.ins > 0 && <span className="niuu-text-state-ok">+{f.ins}</span>}
            {f.del > 0 && <span className="niuu-ml-1 niuu-text-critical">-{f.del}</span>}
          </span>
        </button>
      ))}
      {files.length === 0 && (
        <div className="niuu-p-3 niuu-font-mono niuu-text-xs niuu-text-text-muted">
          no uncommitted changes
        </div>
      )}
    </div>
  );
}

function DiffViewer({ file }: { file: MockDiffFile }) {
  const hunks = buildMockHunks(file);
  return (
    <div
      className="niuu-flex niuu-h-full niuu-flex-col niuu-overflow-auto"
      data-testid="diff-viewer"
    >
      <div className="niuu-flex niuu-flex-shrink-0 niuu-items-center niuu-gap-2 niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-4 niuu-py-3">
        <span
          className={cn(
            'niuu-font-mono niuu-font-medium niuu-text-xs',
            diffStatusColor(file.status),
          )}
        >
          {diffStatusLetter(file.status)}
        </span>
        <span className="niuu-font-mono niuu-text-sm niuu-text-text-primary">{file.path}</span>
        <span className="niuu-font-mono niuu-text-xs niuu-text-text-muted">
          {file.ins > 0 && <span className="niuu-text-state-ok">+{file.ins}</span>}
          {file.del > 0 && <span className="niuu-ml-1 niuu-text-critical">-{file.del}</span>}
        </span>
      </div>
      <div className="niuu-flex-1 niuu-overflow-auto niuu-bg-bg-primary">
        {hunks.map((hunk, i) => (
          <div key={i} className="niuu-font-mono niuu-text-xs">
            <div className="niuu-bg-bg-tertiary niuu-px-4 niuu-py-0.5 niuu-text-text-muted">
              @@ -{hunk.oldStart},{hunk.oldCount} +{hunk.newStart},{hunk.newCount} @@
            </div>
            {hunk.lines.map((line, j) => (
              <div
                key={j}
                className={cn(
                  'niuu-flex niuu-gap-2 niuu-px-4 niuu-py-px',
                  line.type === 'add' &&
                    'niuu-bg-[color-mix(in_srgb,var(--color-brand)_8%,transparent)]',
                  line.type === 'remove' &&
                    'niuu-bg-[color-mix(in_srgb,var(--color-critical)_8%,transparent)]',
                )}
              >
                <span className="niuu-w-8 niuu-flex-shrink-0 niuu-select-none niuu-text-right niuu-text-text-faint">
                  {line.oldLine ?? ''}
                </span>
                <span className="niuu-w-8 niuu-flex-shrink-0 niuu-select-none niuu-text-right niuu-text-text-faint">
                  {line.newLine ?? ''}
                </span>
                <span
                  className={cn(
                    'niuu-w-3 niuu-flex-shrink-0 niuu-select-none',
                    line.type === 'add' && 'niuu-text-state-ok',
                    line.type === 'remove' && 'niuu-text-critical',
                    line.type === 'ctx' && 'niuu-text-text-faint',
                  )}
                >
                  {line.type === 'add' ? '+' : line.type === 'remove' ? '-' : ' '}
                </span>
                <span className="niuu-text-text-primary">{line.content}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function DiffsTab() {
  const [selectedPath, setSelectedPath] = useState<string | null>(MOCK_DIFF_FILES[0]?.path ?? null);
  const selectedFile = MOCK_DIFF_FILES.find((f) => f.path === selectedPath) ?? null;

  return (
    <div className="niuu-grid niuu-h-full niuu-grid-cols-[220px_1fr]" data-testid="diffs-tab">
      <div className="niuu-overflow-hidden niuu-border-r niuu-border-border-subtle niuu-bg-bg-secondary">
        <DiffFileList
          files={MOCK_DIFF_FILES}
          selectedPath={selectedPath}
          onSelect={setSelectedPath}
        />
      </div>
      <div className="niuu-overflow-hidden">
        {selectedFile ? (
          <DiffViewer file={selectedFile} />
        ) : (
          <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-font-mono niuu-text-sm niuu-text-text-muted">
            select a file
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ChronicleTab
// ---------------------------------------------------------------------------

type ChronicleEventType = 'session' | 'git' | 'file' | 'terminal' | 'message' | 'error';

interface ChronicleEvent {
  id: string;
  type: ChronicleEventType;
  label: string;
  ts: number;
  hash?: string;
  ins?: number;
  del?: number;
  exit?: number | null;
}

function buildMockChronicle(): ChronicleEvent[] {
  const now = Date.now();
  return [
    {
      id: 'c-1',
      type: 'session',
      label: 'session started · workspace cloned',
      ts: now - 3_600_000,
    },
    { id: 'c-2', type: 'message', label: 'user: implement the auth handler', ts: now - 3_500_000 },
    { id: 'c-3', type: 'terminal', label: 'npm install', ts: now - 3_480_000, exit: 0 },
    {
      id: 'c-4',
      type: 'file',
      label: 'src/auth/handler.ts · initial implementation',
      ts: now - 3_400_000,
      ins: 42,
      del: 0,
    },
    {
      id: 'c-5',
      type: 'file',
      label: 'src/auth/jwt.ts · add JWT validation',
      ts: now - 3_380_000,
      ins: 96,
      del: 0,
    },
    { id: 'c-6', type: 'terminal', label: 'npm test', ts: now - 3_360_000, exit: 0 },
    {
      id: 'c-7',
      type: 'git',
      label: 'feat(auth): add JWT validation handler',
      ts: now - 3_340_000,
      hash: 'a1b2c3d',
    },
    { id: 'c-8', type: 'message', label: 'user: run the tests', ts: now - 2_400_000 },
    { id: 'c-9', type: 'terminal', label: 'npm test -- --coverage', ts: now - 2_380_000, exit: 0 },
    {
      id: 'c-10',
      type: 'git',
      label: 'test(auth): add coverage for jwt handler',
      ts: now - 2_340_000,
      hash: 'e4f5a6b',
    },
  ];
}

interface ChronicleChapterEvent {
  time: string;
  type: string;
  body: string;
  delta?: string;
  badge?: string;
}

interface ChronicleChapter {
  id: string;
  label: string;
  hash: string;
  title: string;
  age: string;
  span: string;
  count: number;
  events: ChronicleChapterEvent[];
}

const CHRONICLE_CHAPTERS: ChronicleChapter[] = [
  {
    id: 'ch-1',
    label: 'CH. 01',
    hash: 'f2b9c1a',
    title: 'perf: quadtree cull @ 60fps',
    age: '2h ago',
    span: '58m',
    count: 6,
    events: [
      { time: '3h', type: 'SESSION', body: 'pod scheduled on valaskjalf-03' },
      { time: '3h', type: 'GIT', body: 'cloned niuu/volundr@main · switch obs-perf' },
      { time: '2h', type: 'MSG', body: 'USER the drag lags on 400-entity graphs · 38t' },
      { time: '2h', type: 'FILE', body: 'observatory.jsx added quadtree cull', delta: '+214 -12' },
      { time: '2h', type: 'TERM', body: '$ npm test observatory.perf.test.jsx', badge: 'exit 0' },
    ],
  },
  {
    id: 'ch-2',
    label: 'CH. 02',
    hash: 'a019be2',
    title: 'perf: throttle pan to rAF',
    age: '45m ago',
    span: '45m',
    count: 3,
    events: [
      { time: '1h', type: 'MSG', body: 'ASSISTANT frame time dropped 18ms→6ms · 420t' },
      { time: '1h', type: 'FILE', body: 'observatory.jsx throttle pan rAF', delta: '+34 -18' },
    ],
  },
];

function ChronicleTab() {
  const events = buildMockChronicle();
  const commitCount = events.filter((e) => e.type === 'git' && e.hash).length;
  const termCount = events.filter((e) => e.type === 'terminal').length;
  const msgCount = events.filter((e) => e.type === 'message').length;
  const fileCount = new Set(
    events.filter((e) => e.type === 'file').map((e) => e.label.split(' · ')[0] ?? ''),
  ).size;
  return (
    <div className="niuu-min-h-full niuu-bg-bg-primary" data-testid="chronicle-tab">
      <div
        className="niuu-grid niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary"
        style={{ gridTemplateColumns: 'repeat(5,minmax(0,1fr))' }}
        data-testid="chronicle-summary"
      >
        {[
          { label: 'commits', value: commitCount },
          { label: 'files touched', value: fileCount },
          { label: 'shell runs', value: termCount },
          { label: 'messages', value: msgCount },
          { label: 'total span', value: '2h 38m' },
        ].map(({ label, value }) => (
          <div
            key={label}
            className="niuu-flex niuu-flex-col niuu-gap-1 niuu-border-r niuu-border-border-subtle niuu-px-5 niuu-py-3 last:niuu-border-r-0"
          >
            <span className="niuu-font-mono niuu-text-[12px] niuu-font-medium niuu-leading-none niuu-text-text-primary">
              {value}
            </span>
            <span className="niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-faint">
              {label}
            </span>
          </div>
        ))}
      </div>

      <div className="niuu-relative niuu-p-4">
        <div className="niuu-absolute niuu-bottom-4 niuu-left-[22px] niuu-top-4 niuu-w-px niuu-bg-border-subtle" />
        <ol className="niuu-flex niuu-flex-col niuu-gap-4" data-testid="chronicle-timeline">
          {CHRONICLE_CHAPTERS.map((chapter) => (
            <li key={chapter.id} className="niuu-relative">
              <span className="niuu-absolute niuu-left-[5px] niuu-top-6 niuu-h-4 niuu-w-4 niuu-rounded-full niuu-border-2 niuu-border-brand niuu-bg-bg-primary" />
              <section className="niuu-ml-10 niuu-overflow-hidden niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-secondary">
                <div className="niuu-flex niuu-items-center niuu-gap-3 niuu-border-b niuu-border-border-subtle niuu-px-4 niuu-py-3">
                  <span className="niuu-rounded-md niuu-border niuu-border-border niuu-px-2 niuu-py-0.5 niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
                    {chapter.label}
                  </span>
                  <span className="niuu-rounded-md niuu-bg-brand-subtle niuu-px-2 niuu-py-0.5 niuu-font-mono niuu-text-[10px] niuu-font-medium niuu-text-brand">
                    {chapter.hash}
                  </span>
                  <h3 className="niuu-m-0 niuu-text-sm niuu-font-semibold niuu-text-text-primary">
                    {chapter.title}
                  </h3>
                  <div className="niuu-flex-1" />
                  <span className="niuu-font-mono niuu-text-[10px] niuu-text-text-muted">{`${chapter.age}·${chapter.span}·${chapter.count} events`}</span>
                </div>
                <div className="niuu-divide-y niuu-divide-border-subtle">
                  {chapter.events.map((event, index) => (
                    <div
                      key={index}
                      className="niuu-grid niuu-items-center niuu-gap-4 niuu-px-4 niuu-py-3"
                      style={{ gridTemplateColumns: '56px 96px minmax(0,1fr) auto' }}
                      data-testid={`chronicle-event-${event.type.toLowerCase()}`}
                    >
                      <span className="niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
                        {event.time}
                      </span>
                      <span className="niuu-font-mono niuu-text-[11px] niuu-tracking-[0.16em] niuu-text-brand">
                        {event.type}
                      </span>
                      <span className="niuu-text-xs niuu-text-text-secondary">{event.body}</span>
                      <span className="niuu-font-mono niuu-text-[11px]">
                        {event.delta && <span className="niuu-text-brand">{event.delta}</span>}
                        {event.badge && (
                          <span className="niuu-rounded-md niuu-bg-brand-subtle niuu-px-2 niuu-py-0.5 niuu-text-brand">
                            {event.badge}
                          </span>
                        )}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            </li>
          ))}
          <li className="niuu-relative">
            <span className="niuu-absolute niuu-left-[5px] niuu-top-6 niuu-h-4 niuu-w-4 niuu-rounded-full niuu-border-2 niuu-border-border niuu-bg-bg-primary" />
            <section className="niuu-ml-10 niuu-overflow-hidden niuu-rounded-xl niuu-border niuu-border-dashed niuu-border-border niuu-bg-bg-primary">
              <div className="niuu-flex niuu-items-center niuu-gap-3 niuu-border-b niuu-border-border-subtle niuu-px-4 niuu-py-3">
                <span className="niuu-rounded-md niuu-border niuu-border-border niuu-px-2 niuu-py-0.5 niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
                  TAIL
                </span>
                <h3 className="niuu-m-0 niuu-text-sm niuu-font-medium niuu-italic niuu-text-text-muted">
                  in progress — 3 events since last commit
                </h3>
                <div className="niuu-flex-1" />
                <span className="niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
                  22m ago·18m·3 events
                </span>
              </div>
              <div className="niuu-divide-y niuu-divide-border-subtle">
                <div
                  className="niuu-grid niuu-items-center niuu-gap-4 niuu-px-4 niuu-py-3"
                  style={{ gridTemplateColumns: '56px 96px minmax(0,1fr) auto' }}
                >
                  <span className="niuu-font-mono niuu-text-[10px] niuu-text-text-muted">40m</span>
                  <span className="niuu-font-mono niuu-text-[11px] niuu-tracking-[0.16em] niuu-text-brand">
                    TERM
                  </span>
                  <span className="niuu-text-xs niuu-text-text-secondary">
                    $ git push origin obs-perf
                  </span>
                  <span className="niuu-rounded-md niuu-bg-brand-subtle niuu-px-2 niuu-py-0.5 niuu-font-mono niuu-text-brand">
                    exit 0
                  </span>
                </div>
                <div
                  className="niuu-grid niuu-items-center niuu-gap-4 niuu-px-4 niuu-py-3"
                  style={{ gridTemplateColumns: '56px 96px minmax(0,1fr) auto' }}
                >
                  <span className="niuu-font-mono niuu-text-[10px] niuu-text-text-muted">38m</span>
                  <span className="niuu-font-mono niuu-text-[11px] niuu-tracking-[0.16em] niuu-text-brand">
                    SESSION
                  </span>
                  <span className="niuu-text-xs niuu-text-text-secondary">opened PR #248</span>
                  <span />
                </div>
                <div
                  className="niuu-grid niuu-items-center niuu-gap-4 niuu-px-4 niuu-py-3"
                  style={{ gridTemplateColumns: '56px 96px minmax(0,1fr) auto' }}
                >
                  <span className="niuu-font-mono niuu-text-[10px] niuu-text-text-muted">22m</span>
                  <span className="niuu-font-mono niuu-text-[11px] niuu-tracking-[0.16em] niuu-text-brand">
                    TERM
                  </span>
                  <span className="niuu-text-xs niuu-text-text-secondary">$ npm test · watch</span>
                  <span className="niuu-rounded-md niuu-border niuu-border-border niuu-px-2 niuu-py-0.5 niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
                    running…
                  </span>
                </div>
              </div>
            </section>
          </li>
        </ol>
      </div>
    </div>
  );
}

function TerminalWorkspace({
  sessionId,
  stream,
  readOnly,
}: {
  sessionId: string;
  stream: IPtyStream;
  readOnly: boolean;
}) {
  const [tabs, setTabs] = useState([
    { id: 'main', label: 'main', identity: `workspace@${sessionId}` },
    { id: 'tests', label: 'tests', identity: `tests@${sessionId}` },
    { id: 'view-io', label: 'view', badge: 'io' as const, identity: `io@${sessionId}` },
  ]);
  const [activeTerminalTab, setActiveTerminalTab] = useState('main');
  const activeTabMeta = tabs.find((tab) => tab.id === activeTerminalTab) ?? tabs[0];

  return (
    <div className="niuu-flex niuu-h-full niuu-min-h-0 niuu-flex-col niuu-bg-bg-primary">
      <div className="niuu-flex niuu-items-center niuu-justify-between niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-3 niuu-py-2">
        <div className="niuu-flex niuu-items-center niuu-gap-1.5">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTerminalTab(tab.id)}
              className={cn(
                'niuu-flex niuu-items-center niuu-gap-2 niuu-rounded-md niuu-border niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-[11px]',
                activeTerminalTab === tab.id
                  ? 'niuu-border-border niuu-bg-bg-elevated niuu-text-text-primary'
                  : 'niuu-border-transparent niuu-text-text-muted hover:niuu-border-border-subtle hover:niuu-text-text-secondary',
              )}
              data-testid={`terminal-tab-${tab.id}`}
            >
              <span className="niuu-text-brand">{'>_'}</span>
              <span>{tab.label}</span>
              {tab.badge && (
                <span className="niuu-rounded-sm niuu-bg-brand-subtle niuu-px-1.5 niuu-py-0.5 niuu-text-[10px] niuu-text-brand">
                  {tab.badge}
                </span>
              )}
            </button>
          ))}
          <button
            type="button"
            onClick={() =>
              setTabs((current) => [
                ...current,
                {
                  id: `term-${current.length + 1}`,
                  label: `term-${current.length + 1}`,
                  identity: `shell-${current.length + 1}@${sessionId}`,
                },
              ])
            }
            className="niuu-rounded-md niuu-border niuu-border-transparent niuu-px-2 niuu-py-1.5 niuu-font-mono niuu-text-[12px] niuu-text-text-muted hover:niuu-border-border-subtle hover:niuu-text-text-secondary"
            aria-label="Add terminal tab"
            data-testid="terminal-tab-add"
          >
            +
          </button>
        </div>
        <div className="niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
          {activeTabMeta?.identity}
        </div>
      </div>
      <div className="niuu-min-h-0 niuu-flex-1 niuu-overflow-hidden">
        <div
          key={activeTerminalTab}
          className="niuu-h-full niuu-min-h-0"
          data-testid={`terminal-panel-${activeTerminalTab}`}
        >
          <Terminal
            sessionId={`${sessionId}::${activeTerminalTab}`}
            stream={stream}
            readOnly={readOnly}
            className="niuu-h-full"
          />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// LogsTab
// ---------------------------------------------------------------------------

type LogLevel = 'debug' | 'info' | 'warn' | 'error';

interface LogLine {
  id: string;
  ts: string;
  level: LogLevel;
  src: string;
  msg: string;
}

const LOG_LEVEL_CLASSES: Record<LogLevel, string> = {
  debug: 'niuu-text-text-faint',
  info: 'niuu-text-text-secondary',
  warn: 'niuu-text-state-warn',
  error: 'niuu-text-critical',
};

const MOCK_LOGS: LogLine[] = [
  { id: 'l-1', ts: '10:00:01', level: 'info', src: 'skuld', msg: 'session starting' },
  {
    id: 'l-2',
    ts: '10:00:02',
    level: 'info',
    src: 'skuld',
    msg: 'workspace cloned: niuulabs/volundr',
  },
  {
    id: 'l-3',
    ts: '10:00:05',
    level: 'debug',
    src: 'ravn',
    msg: 'model loaded: claude-sonnet-4-6',
  },
  { id: 'l-4', ts: '10:00:06', level: 'info', src: 'ravn', msg: 'mesh room joined: room-ds-1' },
  {
    id: 'l-5',
    ts: '10:01:14',
    level: 'info',
    src: 'ravn',
    msg: 'tool: read_file src/auth/handler.ts',
  },
  { id: 'l-6', ts: '10:01:15', level: 'debug', src: 'ravn', msg: 'tool ok · 45ms · 2.1kb' },
  {
    id: 'l-7',
    ts: '10:02:30',
    level: 'info',
    src: 'ravn',
    msg: 'tool: write_file src/auth/jwt.ts',
  },
  { id: 'l-8', ts: '10:02:31', level: 'debug', src: 'ravn', msg: 'tool ok · 12ms' },
  {
    id: 'l-9',
    ts: '10:04:00',
    level: 'warn',
    src: 'skuld',
    msg: 'cpu approaching limit: 1.8/2.0c',
  },
  { id: 'l-10', ts: '10:05:22', level: 'info', src: 'ravn', msg: 'tool: run_command npm test' },
  { id: 'l-11', ts: '10:05:23', level: 'debug', src: 'ravn', msg: 'exit 0 · 1.2s' },
  {
    id: 'l-12',
    ts: '10:06:00',
    level: 'info',
    src: 'skuld',
    msg: 'commit: feat(auth): add JWT validation handler',
  },
];

function LogsTab() {
  const [levelFilter, setLevelFilter] = useState<LogLevel | 'all'>('all');

  const filtered =
    levelFilter === 'all' ? MOCK_LOGS : MOCK_LOGS.filter((l) => l.level === levelFilter);

  return (
    <div className="niuu-flex niuu-h-full niuu-flex-col" data-testid="logs-tab">
      {/* Filter bar */}
      <div className="niuu-flex niuu-flex-shrink-0 niuu-items-center niuu-gap-1 niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-4 niuu-py-3">
        {(['all', 'error', 'warn', 'info', 'debug'] as const).map((lvl) => (
          <button
            key={lvl}
            onClick={() => setLevelFilter(lvl)}
            className={cn(
              'niuu-rounded-md niuu-border niuu-px-2.5 niuu-py-1 niuu-font-mono niuu-text-xs',
              levelFilter === lvl
                ? 'niuu-border-brand/50 niuu-bg-bg-elevated niuu-text-brand'
                : 'niuu-border-transparent niuu-text-text-muted hover:niuu-border-border-subtle hover:niuu-text-text-secondary',
            )}
            data-testid={`log-filter-${lvl}`}
          >
            {lvl}
          </button>
        ))}
      </div>

      {/* Log body */}
      <div
        className="niuu-flex-1 niuu-overflow-auto niuu-bg-bg-primary niuu-px-4 niuu-py-3"
        data-testid="logs-body"
      >
        {filtered.map((line) => (
          <div
            key={line.id}
            className="niuu-flex niuu-gap-3 niuu-border-b niuu-border-border-subtle niuu-py-1.5 niuu-font-mono niuu-text-xs"
            data-testid={`log-line-${line.level}`}
          >
            <span className="niuu-w-14 niuu-flex-shrink-0 niuu-text-text-faint">{line.ts}</span>
            <span
              className={cn(
                'niuu-w-10 niuu-flex-shrink-0 niuu-font-medium niuu-uppercase',
                LOG_LEVEL_CLASSES[line.level],
              )}
            >
              {line.level}
            </span>
            <span className="niuu-w-12 niuu-flex-shrink-0 niuu-text-text-faint">{line.src}</span>
            <span className="niuu-text-text-primary">{line.msg}</span>
          </div>
        ))}
        {filtered.length === 0 && (
          <div className="niuu-py-4 niuu-text-center niuu-text-text-muted">no log entries</div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

/** Six-tab session detail page (Chat / Terminal / Diffs / Files / Chronicle / Logs). */
export function SessionDetailPage({
  sessionId,
  readOnly = false,
  initialTab = 'chat',
}: SessionDetailPageProps) {
  const [activeTab, setActiveTab] = useState<SessionTab>(initialTab);
  const [showRes, setShowRes] = useState(false);

  const ptyStream = useService<IPtyStream>('ptyStream');
  const filesystem = useService<IFileSystemPort>('filesystem');

  const sessionQuery = useSessionDetail(sessionId);

  const session = sessionQuery.data;

  return (
    <div className="niuu-flex niuu-h-full niuu-flex-col" data-testid="session-detail-page">
      {/* Header */}
      {session ? (
        <SessionHeader session={session} readOnly={readOnly} showRes={showRes} />
      ) : (
        <div className="niuu-flex niuu-items-center niuu-gap-3 niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-4 niuu-py-2">
          <span
            className="niuu-font-mono niuu-text-sm niuu-text-text-primary"
            data-testid="session-id-label"
          >
            {sessionId}
          </span>
          {readOnly && (
            <span
              className="niuu-rounded niuu-bg-bg-elevated niuu-px-2 niuu-py-0.5 niuu-text-xs niuu-text-text-muted"
              data-testid="session-archived-badge"
            >
              archived
            </span>
          )}
        </div>
      )}

      {/* Tab bar */}
      <div
        className="niuu-flex niuu-items-center niuu-gap-0 niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-3"
        role="tablist"
        aria-label="Session tabs"
      >
        {TABS.map((tab) => {
          const count = session ? tabCount(tab.id, session) : undefined;
          return (
            <button
              key={tab.id}
              id={`tab-${tab.id}`}
              role="tab"
              aria-selected={activeTab === tab.id}
              data-testid={`tab-${tab.id}`}
              onClick={() => setActiveTab(tab.id)}
              className={
                activeTab === tab.id
                  ? 'niuu-flex niuu-items-center niuu-gap-2 niuu-border-b-2 niuu-border-brand niuu-px-3 niuu-py-2.5 niuu-font-mono niuu-text-[13px] niuu-font-medium niuu-text-brand'
                  : 'niuu-flex niuu-items-center niuu-gap-2 niuu-border-b-2 niuu-border-transparent niuu-px-3 niuu-py-2.5 niuu-font-mono niuu-text-[13px] niuu-text-text-muted hover:niuu-text-text-secondary'
              }
            >
              <span className="niuu-inline-flex niuu-w-4 niuu-justify-center niuu-font-mono niuu-text-[11px] niuu-opacity-70">
                {tabIcon(tab.id)}
              </span>
              <span>{tab.label}</span>
              {count != null && count > 0 && (
                <span className="niuu-rounded-full niuu-bg-bg-elevated niuu-px-2 niuu-py-0.5 niuu-font-mono niuu-text-[10px] niuu-opacity-80">
                  {count}
                </span>
              )}
            </button>
          );
        })}
        <div className="niuu-flex-1" />
        <div className="niuu-flex niuu-items-center niuu-gap-1 niuu-pr-2">
          <button
            type="button"
            onClick={() => setShowRes((v) => !v)}
            className={cn(
              'niuu-rounded-md niuu-border niuu-border-transparent niuu-px-2.5 niuu-py-1 niuu-font-mono niuu-text-[12px]',
              showRes
                ? 'niuu-border-border niuu-bg-bg-elevated niuu-text-brand'
                : 'niuu-text-text-muted hover:niuu-border-border-subtle hover:niuu-bg-bg-elevated/60',
            )}
            data-testid="resources-toggle"
          >
            res
          </button>
          <button
            type="button"
            aria-label="stop session"
            className="niuu-rounded-md niuu-border niuu-border-transparent niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[12px] niuu-text-text-muted hover:niuu-border-border-subtle hover:niuu-bg-bg-elevated/60"
          >
            stop
          </button>
          <button
            type="button"
            aria-label="archive session"
            className="niuu-rounded-md niuu-border niuu-border-transparent niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[12px] niuu-text-text-muted hover:niuu-border-border-subtle hover:niuu-bg-bg-elevated/60"
          >
            arc
          </button>
          <button
            type="button"
            aria-label="delete session"
            className="niuu-rounded-md niuu-border niuu-border-transparent niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[12px] niuu-text-text-muted hover:niuu-border-critical hover:niuu-bg-critical-bg"
          >
            del
          </button>
        </div>
      </div>

      {/* Tab panels */}
      <div className="niuu-min-h-0 niuu-flex-1 niuu-overflow-hidden">
        {activeTab === 'chat' && (
          <div role="tabpanel" aria-labelledby="tab-chat" className="niuu-h-full niuu-min-h-0">
            {sessionQuery.isLoading && <LoadingState label="Loading session\u2026" />}
            {sessionQuery.isError && (
              <ErrorState
                title="Failed to load session"
                message={
                  sessionQuery.error instanceof Error ? sessionQuery.error.message : 'Unknown error'
                }
              />
            )}
            {session && <ChatTab session={session} />}
          </div>
        )}

        {activeTab === 'terminal' && (
          <div role="tabpanel" aria-labelledby="tab-terminal" className="niuu-h-full niuu-min-h-0">
            <TerminalWorkspace sessionId={sessionId} stream={ptyStream} readOnly={readOnly} />
          </div>
        )}

        {activeTab === 'diffs' && (
          <div role="tabpanel" aria-labelledby="tab-diffs" className="niuu-h-full niuu-min-h-0">
            <DiffsTab />
          </div>
        )}

        {activeTab === 'files' && (
          <div role="tabpanel" aria-labelledby="tab-files" className="niuu-h-full niuu-min-h-0">
            <SessionFilesWorkspace sessionId={sessionId} filesystem={filesystem} />
          </div>
        )}

        {activeTab === 'chronicle' && (
          <div
            role="tabpanel"
            aria-labelledby="tab-chronicle"
            className="niuu-h-full niuu-min-h-0 niuu-overflow-y-auto niuu-bg-bg-primary"
          >
            <ChronicleTab />
          </div>
        )}

        {activeTab === 'logs' && (
          <div role="tabpanel" aria-labelledby="tab-logs" className="niuu-h-full niuu-min-h-0">
            <LogsTab />
          </div>
        )}
      </div>
    </div>
  );
}
