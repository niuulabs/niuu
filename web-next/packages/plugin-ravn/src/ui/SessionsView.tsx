import { useEffect, useMemo, useRef, useState } from 'react';
import {
  PersonaAvatar,
  ErrorState,
  LoadingState,
  SessionChat,
  cn,
  normalizeSessionUrl,
  useSkuldChat,
} from '@niuulabs/ui';
import { useService } from '@niuulabs/plugin-sdk';
import { LiveLogsTab, TelemetryTab, type IVolundrService } from '@niuulabs/plugin-volundr';
import type { PersonaRole } from '@niuulabs/domain';
import { Eye, EyeOff, FileCode2, MessageSquareText, Sparkles } from 'lucide-react';
import { useMessages, useSessions } from './hooks/useSessions';
import { useRavens } from './hooks/useRavens';
import { useRavnBudget } from './hooks/useBudget';
import { usePersona } from './usePersona';
import { loadStorage, saveStorage } from './storage';
import type { Message } from '../domain/message';
import type { Session } from '../domain/session';
import type { PersonaDetail } from '../ports';
import type { Ravn } from '../domain/ravn';
import './SessionsView.css';

const SESSION_STORAGE_KEY = 'ravn.session';

type TranscriptFilter = 'all' | 'chat' | 'tools' | 'system';
type SessionSurfaceTab = 'chat' | 'trace' | 'logs';

type TimelineTone = 'info' | 'muted' | 'warn' | 'good';

interface TranscriptEntry {
  id: string;
  kind: 'system' | 'user' | 'thought' | 'tool' | 'assistant' | 'emit';
  ts: string;
  text?: string;
  toolName?: string;
  args?: string;
  result?: string;
  eventName?: string;
  attrs?: string[];
}

interface TimelineEntry {
  id: string;
  ts: string;
  label: string;
  tone: TimelineTone;
}

const FILTER_OPTIONS: Array<{ value: TranscriptFilter; label: string }> = [
  { value: 'all', label: 'all' },
  { value: 'chat', label: 'chat only' },
  { value: 'tools', label: '+ tools' },
  { value: 'system', label: '+ system' },
];

const SESSION_SURFACE_TABS: Array<{
  id: SessionSurfaceTab;
  label: string;
  icon: typeof MessageSquareText;
}> = [
  { id: 'chat', label: 'Chat', icon: MessageSquareText },
  { id: 'trace', label: 'Trace', icon: Sparkles },
  { id: 'logs', label: 'Logs', icon: FileCode2 },
];

const DEFAULT_PERSONA_BY_ROLE: Partial<Record<PersonaRole, string>> = {
  arbiter: 'review-arbiter',
  autonomy: 'autonomous-agent',
  build: 'coder',
  coord: 'coordinator',
  investigate: 'investigator',
  knowledge: 'mimir-curator',
  observe: 'health-auditor',
  plan: 'architect',
  qa: 'verifier',
  report: 'reporter',
  review: 'reviewer',
};

export function normalizeLabel(value: string | undefined): string {
  if (!value) return '—';
  return value.replace(/_/g, ' ').replace(/-/g, ' ');
}

export function formatTokenCount(value: number | undefined): string {
  if (value == null) return '—';
  return value >= 1000 ? `${(value / 1000).toFixed(1)}k` : String(value);
}

export function formatCurrency(value: number | undefined): string {
  if (value == null) return '—';
  return `$${value.toFixed(2)}`;
}

export function formatShortTime(iso: string): string {
  return iso.slice(11, 19);
}

export function formatTimelineStamp(iso: string): string {
  return `${iso.slice(11, 16)} ${iso.slice(0, 10)}`;
}

export function shortSessionId(session: Session): string {
  return `s-${session.id.slice(-3)}`;
}

export function derivePersonaKey(session: Session): string {
  const title = (session.title ?? '').toLowerCase();
  if (session.personaRole === 'review' && /(pr|review)/.test(title)) return 'review-arbiter';
  if (session.personaRole === 'qa' && /(integration|test)/.test(title)) return 'verifier';
  if (session.personaRole === 'plan' && /(sprint|plan)/.test(title)) return 'architect';
  return DEFAULT_PERSONA_BY_ROLE[session.personaRole ?? 'build'] ?? 'coder';
}

export function deriveTrigger(session: Session): string {
  const title = (session.title ?? '').toLowerCase();
  if (session.personaRole === 'review' && /(pr|review)/.test(title)) return 'pr-review';
  if (session.personaRole === 'observe') return 'cron.hourly';
  if (session.personaRole === 'knowledge') return 'docs.sync';
  if (session.personaRole === 'report') return 'cron.daily';
  if (session.personaRole === 'qa') return 'qa-suite';
  if (session.personaRole === 'coord') return 'deploy-orchestrator';
  if (session.personaRole === 'plan') return 'planning-request';
  return 'manual';
}

export function titleForSession(session: Session): string {
  return session.title ?? `Session ${session.id.slice(0, 8)}`;
}

export function buildInitLine(ravnName: string, trigger: string): string {
  return `session init · raven=${ravnName} · trigger=${trigger}`;
}

export function taskLine(session: Session, trigger: string): string {
  if (trigger === 'manual') return `Manual: ${titleForSession(session)}`;
  return `Triggered by ${trigger}: ${titleForSession(session)}`;
}

export function stripBraces(value: string): string {
  return value.replace(/^\{+|\}+$/g, '').trim();
}

export function previewJson(value: string): string {
  try {
    const parsed = JSON.parse(value) as unknown;
    if (typeof parsed === 'string') return parsed;
    if (parsed && typeof parsed === 'object') {
      const entries = Object.entries(parsed as Record<string, unknown>);
      if (typeof (parsed as { path?: unknown }).path === 'string') {
        return String((parsed as { path: string }).path);
      }
      if (typeof (parsed as { content?: unknown }).content === 'string') {
        return String((parsed as { content: string }).content).replace(/^\/\/\s*/, '');
      }
      if (entries.length > 0) {
        return entries
          .slice(0, 3)
          .map(([key, item]) => `${key}=${typeof item === 'string' ? item : JSON.stringify(item)}`)
          .join(' ');
      }
    }
  } catch {
    return value;
  }
  return value;
}

export function parseEmit(value: string): { eventName: string; attrs: string[] } {
  try {
    const parsed = JSON.parse(value) as { event?: string; payload?: Record<string, unknown> };
    const attrs =
      parsed.payload && typeof parsed.payload === 'object'
        ? Object.entries(parsed.payload).map(([key, item]) => `${key}: ${String(item)}`)
        : [];
    return {
      eventName: parsed.event ?? 'event',
      attrs,
    };
  } catch {
    return { eventName: stripBraces(value), attrs: [] };
  }
}

export function synthesizeTranscript(
  session: Session,
  ravn: Ravn | null,
  personaName: string,
  trigger: string,
): TranscriptEntry[] {
  const startedAt = session.createdAt;
  const thinking =
    session.status === 'running'
      ? `Persona=${personaName}. Working through ${titleForSession(session).toLowerCase()}.`
      : `Persona=${personaName}. ${titleForSession(session)} is ready for wrap-up.`;

  const entries: TranscriptEntry[] = [
    {
      id: `${session.id}-system`,
      kind: 'system',
      ts: startedAt,
      text: buildInitLine(ravn?.personaName ?? session.personaName, trigger),
    },
    {
      id: `${session.id}-user`,
      kind: 'user',
      ts: startedAt,
      text: taskLine(session, trigger),
    },
    {
      id: `${session.id}-think`,
      kind: 'thought',
      ts: startedAt,
      text: thinking,
    },
  ];

  if (session.status === 'running') {
    entries.push({
      id: `${session.id}-tool`,
      kind: 'tool',
      ts: startedAt,
      toolName: 'read',
      args: '…',
      result: 'loaded',
    });
    return entries;
  }

  entries.push({
    id: `${session.id}-assistant`,
    kind: 'assistant',
    ts: startedAt,
    text: `${personaName} finished ${titleForSession(session).toLowerCase()}.`,
  });

  if (session.status === 'stopped') {
    entries.push({
      id: `${session.id}-system-stop`,
      kind: 'system',
      ts: startedAt,
      text: 'session closed · read-only',
    });
  } else if (session.status === 'failed') {
    entries.push({
      id: `${session.id}-system-failed`,
      kind: 'system',
      ts: startedAt,
      text: 'session aborted · budget exceeded',
    });
  } else {
    entries.push({
      id: `${session.id}-emit`,
      kind: 'emit',
      ts: startedAt,
      eventName: 'work.completed',
    });
  }

  return entries;
}

export function buildTranscript(
  session: Session,
  ravn: Ravn | null,
  personaName: string,
  messages: Message[],
): TranscriptEntry[] {
  const trigger = deriveTrigger(session);
  if (messages.length === 0) {
    return synthesizeTranscript(session, ravn, personaName, trigger);
  }

  const entries: TranscriptEntry[] = [
    {
      id: `${session.id}-system`,
      kind: 'system',
      ts: session.createdAt,
      text: buildInitLine(ravn?.personaName ?? session.personaName, trigger),
    },
  ];

  for (let index = 0; index < messages.length; index += 1) {
    const message = messages[index]!;
    const next = messages[index + 1];

    if (
      message.kind === 'tool_call' &&
      next &&
      next.kind === 'tool_result' &&
      next.toolName === message.toolName
    ) {
      entries.push({
        id: `${message.id}-${next.id}`,
        kind: 'tool',
        ts: next.ts,
        toolName: message.toolName ?? 'tool',
        args: previewJson(message.content),
        result: previewJson(next.content),
      });
      index += 1;
      continue;
    }

    switch (message.kind) {
      case 'user':
        entries.push({
          id: message.id,
          kind: 'user',
          ts: message.ts,
          text: message.content,
        });
        break;
      case 'think':
        entries.push({
          id: message.id,
          kind: 'thought',
          ts: message.ts,
          text: message.content,
        });
        break;
      case 'tool_call':
        entries.push({
          id: message.id,
          kind: 'tool',
          ts: message.ts,
          toolName: message.toolName ?? 'tool',
          args: previewJson(message.content),
          result: 'running',
        });
        break;
      case 'tool_result':
        entries.push({
          id: message.id,
          kind: 'tool',
          ts: message.ts,
          toolName: message.toolName ?? 'tool',
          args: '…',
          result: previewJson(message.content),
        });
        break;
      case 'asst':
        entries.push({
          id: message.id,
          kind: 'assistant',
          ts: message.ts,
          text: message.content,
        });
        break;
      case 'emit': {
        const parsed = parseEmit(message.content);
        entries.push({
          id: message.id,
          kind: 'emit',
          ts: message.ts,
          eventName: parsed.eventName,
          attrs: parsed.attrs,
        });
        break;
      }
      case 'system':
        entries.push({
          id: message.id,
          kind: 'system',
          ts: message.ts,
          text: message.content,
        });
        break;
    }
  }

  return entries;
}

export function filterTranscript(
  entries: TranscriptEntry[],
  filter: TranscriptFilter,
): TranscriptEntry[] {
  switch (filter) {
    case 'chat':
      return entries.filter((entry) =>
        ['user', 'thought', 'assistant', 'emit'].includes(entry.kind),
      );
    case 'tools':
      return entries.filter((entry) =>
        ['user', 'thought', 'assistant', 'emit', 'tool'].includes(entry.kind),
      );
    case 'system':
      return entries.filter((entry) =>
        ['user', 'thought', 'assistant', 'emit', 'system'].includes(entry.kind),
      );
    default:
      return entries;
  }
}

export function summarizeSession(
  session: Session,
  entries: TranscriptEntry[],
  personaLabel: string,
): string {
  const thought = [...entries].reverse().find((entry) => entry.kind === 'thought' && entry.text);
  if (thought?.text) return thought.text.replace(/^Persona=[^.]+\.\s*/, '');

  const assistant = [...entries]
    .reverse()
    .find((entry) => entry.kind === 'assistant' && entry.text);
  if (assistant?.text) return assistant.text;

  if (session.status === 'running') {
    return `${personaLabel} is working through ${titleForSession(session).toLowerCase()}.`;
  }

  return `${personaLabel} wrapped ${titleForSession(session).toLowerCase()}.`;
}

export function deriveTimeline(entries: TranscriptEntry[], session: Session): TimelineEntry[] {
  const started: TimelineEntry = {
    id: `${session.id}-started`,
    ts: session.createdAt,
    label: `started · ${formatTimelineStamp(session.createdAt)}`,
    tone: 'info',
  };

  const rest = entries.map((entry) => {
    let label: string = entry.kind;
    let tone: TimelineTone = 'muted';

    switch (entry.kind) {
      case 'system':
        label = 'session init';
        tone = 'info';
        break;
      case 'user':
        label = 'user instruction';
        tone = 'info';
        break;
      case 'thought':
        label = 'reasoning';
        break;
      case 'tool':
        label = `tool · ${entry.toolName ?? 'tool'}`;
        tone = 'warn';
        break;
      case 'assistant':
        label = 'raven answer';
        tone = 'good';
        break;
      case 'emit':
        label = `emit · ${entry.eventName ?? 'event'}`;
        tone = 'good';
        break;
    }

    return {
      id: entry.id,
      ts: entry.ts,
      label,
      tone,
    };
  });

  return [started, ...rest].slice(0, 6);
}

export function deriveRelativeAge(iso: string, anchorIso: string): string {
  const deltaMs = new Date(anchorIso).getTime() - new Date(iso).getTime();
  const deltaMinutes = Math.max(1, Math.round(deltaMs / 60000));
  if (deltaMinutes < 60) return `${deltaMinutes}m`;
  const deltaHours = Math.round(deltaMinutes / 60);
  return `${deltaHours}h`;
}

export function deriveAnchorTime(sessions: Session[]): string {
  if (sessions.length === 0) return new Date().toISOString();
  const newest = [...sessions].sort((left, right) =>
    right.createdAt.localeCompare(left.createdAt),
  )[0]!;
  return new Date(new Date(newest.createdAt).getTime() + 4 * 60 * 1000).toISOString();
}

export function sessionIdentityKey(session: Pick<Session, 'id' | 'ravnId' | 'instanceId'>): string {
  return session.instanceId
    ? `${encodeURIComponent(session.instanceId)}:${encodeURIComponent(session.ravnId)}:${session.id}`
    : session.id;
}

function ravenIdentityKey(id: string, instanceId?: string): string {
  return instanceId ? `${encodeURIComponent(instanceId)}:${id}` : id;
}

export function pickDefaultSession(sessions: Session[], preferredId: string | null): string | null {
  if (sessions.length === 0) return null;
  if (preferredId && sessions.some((session) => sessionIdentityKey(session) === preferredId)) {
    return preferredId;
  }
  const sorted = [...sessions].sort((left, right) => right.createdAt.localeCompare(left.createdAt));
  return sessionIdentityKey(sorted.find((session) => session.status === 'running') ?? sorted[0]!);
}

function preferredSessionId(): string | null {
  const params = new URLSearchParams(window.location.search);
  const fromUrl = params.get('session');
  const instanceId = params.get('instance_id');
  const ravnId = params.get('ravn_id');
  if (fromUrl) {
    return instanceId && ravnId
      ? `${encodeURIComponent(instanceId)}:${encodeURIComponent(ravnId)}:${fromUrl}`
      : fromUrl;
  }
  return loadStorage<string | null>(SESSION_STORAGE_KEY, null);
}

function SessionRailItem({
  session,
  selected,
  relativeAge,
  onSelect,
}: {
  session: Session;
  selected: boolean;
  relativeAge: string;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className={cn('rv-rs__session', selected && 'rv-rs__session--selected')}
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`Open session ${titleForSession(session)}`}
    >
      <div className="rv-rs__session-avatar">
        <PersonaAvatar
          role={session.personaRole ?? 'build'}
          letter={session.personaLetter ?? '?'}
          size={24}
        />
      </div>
      <div className="rv-rs__session-main">
        <div className="rv-rs__session-title">{titleForSession(session)}</div>
        <div className="rv-rs__session-meta">
          <span>{session.personaName}</span>
          <span>·</span>
          <span>{relativeAge}</span>
          <span>·</span>
          <span>{formatCurrency(session.costUsd)}</span>
        </div>
      </div>
    </button>
  );
}

function SessionRailGroup({
  label,
  count,
  sessions,
  selectedId,
  anchorTime,
  onSelect,
}: {
  label: string;
  count: number;
  sessions: Session[];
  selectedId: string | null;
  anchorTime: string;
  onSelect: (session: Session) => void;
}) {
  if (sessions.length === 0) return null;

  return (
    <section className="rv-rs__group">
      <div className="rv-rs__group-head">
        <span className="rv-rs__group-label">{label}</span>
        <span className="rv-rs__group-count">{count}</span>
      </div>
      <div className="rv-rs__group-body">
        {sessions.map((session) => (
          <SessionRailItem
            key={sessionIdentityKey(session)}
            session={session}
            selected={selectedId === sessionIdentityKey(session)}
            relativeAge={deriveRelativeAge(session.createdAt, anchorTime)}
            onSelect={() => onSelect(session)}
          />
        ))}
      </div>
    </section>
  );
}

function CollapsedSessionRail({
  sessions,
  selectedId,
  onSelect,
}: {
  sessions: Session[];
  selectedId: string | null;
  onSelect: (session: Session) => void;
}) {
  return (
    <div className="rv-rs__rail-collapsed-body">
      {sessions.map((session) => (
        <button
          key={sessionIdentityKey(session)}
          type="button"
          className={cn(
            'rv-rs__rail-collapsed-item',
            selectedId === sessionIdentityKey(session) && 'rv-rs__rail-collapsed-item--selected',
          )}
          onClick={() => onSelect(session)}
          aria-label={`Open session ${titleForSession(session)}`}
        >
          <PersonaAvatar
            role={session.personaRole ?? 'build'}
            letter={session.personaLetter ?? '?'}
            size={24}
          />
        </button>
      ))}
    </div>
  );
}

function SessionHeader({
  session,
  ravn,
  personaLabel,
  showInternalMessages,
  onToggleInternalMessages,
}: {
  session: Session;
  ravn: Ravn | null;
  personaLabel: string;
  showInternalMessages: boolean;
  onToggleInternalMessages: () => void;
}) {
  const isRunning = session.status === 'running';
  const hasLiveChat = isRunning && Boolean(normalizeSessionUrl(session.chatEndpoint ?? null));
  const InternalIcon = showInternalMessages ? EyeOff : Eye;

  return (
    <header className="rv-rs__head" data-testid="sessions-header">
      <div className="rv-rs__head-left">
        <div className="rv-rs__head-avatar">
          <PersonaAvatar
            role={session.personaRole ?? 'build'}
            letter={session.personaLetter ?? '?'}
            size={34}
          />
        </div>
        <div className="rv-rs__head-copy">
          <div className="rv-rs__title-row">
            <h2 className="rv-rs__title">{titleForSession(session)}</h2>
            <span
              className={cn(
                'rv-rs__status',
                isRunning ? 'rv-rs__status--live' : 'rv-rs__status--closed',
              )}
            >
              <span className="rv-rs__status-dot" />
              {isRunning ? 'active' : session.status}
            </span>
          </div>
          <div className="rv-rs__meta-line">
            <span>{shortSessionId(session)}</span>
            <span>·</span>
            <span>
              raven:{' '}
              <strong>{ravn?.residentName || ravn?.personaName || session.personaName}</strong>
            </span>
            <span>·</span>
            <span>
              persona: <strong>{personaLabel}</strong>
            </span>
            <span>·</span>
            <span>
              trigger: <strong>{deriveTrigger(session)}</strong>
            </span>
          </div>
        </div>
      </div>
      <div className="rv-rs__head-right">
        <div className="rv-rs__metrics" data-testid="sessions-metrics">
          <span>
            msgs <strong>{session.messageCount ?? 0}</strong>
          </span>
          <span>
            tokens <strong>{formatTokenCount(session.tokenCount)}</strong>
          </span>
          <span>
            cost <strong>{formatCurrency(session.costUsd)}</strong>
          </span>
        </div>
        <div className="rv-rs__actions" data-testid="sessions-actions">
          {hasLiveChat && (
            <button
              type="button"
              className={cn(
                'rv-rs__action-btn',
                showInternalMessages && 'rv-rs__action-btn--active',
              )}
              aria-label={
                showInternalMessages ? 'Hide tool calls and results' : 'Show tool calls and results'
              }
              aria-pressed={showInternalMessages}
              data-testid="internal-toggle"
              onClick={onToggleInternalMessages}
            >
              <InternalIcon size={14} aria-hidden="true" />
            </button>
          )}
        </div>
      </div>
    </header>
  );
}

function TranscriptToolbar({
  filter,
  onFilterChange,
}: {
  filter: TranscriptFilter;
  onFilterChange: (value: TranscriptFilter) => void;
}) {
  return (
    <div className="rv-rs__toolbar">
      <div className="rv-rs__toolbar-left">
        <span className="rv-rs__toolbar-label">filter:</span>
        <div className="rv-rs__filter-group" role="group" aria-label="Session transcript filter">
          {FILTER_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              className={cn(
                'rv-rs__filter-btn',
                filter === option.value && 'rv-rs__filter-btn--active',
              )}
              onClick={() => onFilterChange(option.value)}
              aria-pressed={filter === option.value}
            >
              {option.label}
            </button>
          ))}
        </div>
        <span className="rv-rs__toolbar-dot">·</span>
        <span className="rv-rs__toolbar-label">jump:</span>
        <div className="rv-rs__kbd-group">
          <span className="rv-rs__kbd">j</span>
          <span className="rv-rs__kbd">k</span>
        </div>
      </div>
      <div className="rv-rs__follow">
        <span className="rv-rs__follow-dot" />
        <span>following tail</span>
      </div>
    </div>
  );
}

function TranscriptMessage({
  entry,
  personaLabel,
  personaLetter,
  personaRole,
}: {
  entry: TranscriptEntry;
  personaLabel: string;
  personaLetter: string;
  personaRole: PersonaRole;
}) {
  if (entry.kind === 'system') {
    return (
      <div className="rv-rs__msg rv-rs__msg--system" data-kind="system">
        <div className="rv-rs__msg-rail">sys</div>
        <div className="rv-rs__system-line">
          {entry.text}
          <span className="rv-rs__msg-ts">· {formatShortTime(entry.ts)}</span>
        </div>
      </div>
    );
  }

  if (entry.kind === 'user') {
    return (
      <div className="rv-rs__msg rv-rs__msg--user" data-kind="user">
        <div className="rv-rs__msg-rail rv-rs__msg-rail--accent">you</div>
        <div className="rv-rs__msg-body">
          <div className="rv-rs__msg-text rv-rs__msg-text--user">{entry.text}</div>
          <div className="rv-rs__msg-time">{formatShortTime(entry.ts)}</div>
        </div>
      </div>
    );
  }

  if (entry.kind === 'thought') {
    return (
      <div className="rv-rs__msg rv-rs__msg--thought" data-kind="thought">
        <div className="rv-rs__msg-rail">∴</div>
        <div className="rv-rs__msg-body">
          <div className="rv-rs__msg-author">thought · {formatShortTime(entry.ts)}</div>
          <div className="rv-rs__thought">{entry.text}</div>
        </div>
      </div>
    );
  }

  if (entry.kind === 'tool') {
    return (
      <div className="rv-rs__msg rv-rs__msg--tool" data-kind="tool">
        <div className="rv-rs__msg-rail">⌁</div>
        <div className="rv-rs__tool-line">
          <span className="rv-rs__tool-name">{entry.toolName}</span>
          <span className="rv-rs__tool-paren">(</span>
          <span className="rv-rs__tool-args">{entry.args}</span>
          <span className="rv-rs__tool-paren">)</span>
          <span className="rv-rs__tool-arrow">→</span>
          <span className="rv-rs__tool-result">{entry.result}</span>
          <span className="rv-rs__msg-ts">· {formatShortTime(entry.ts)}</span>
        </div>
      </div>
    );
  }

  if (entry.kind === 'emit') {
    return (
      <div className="rv-rs__msg rv-rs__msg--emit" data-kind="emit">
        <div className="rv-rs__msg-rail">↗</div>
        <div className="rv-rs__msg-body">
          <div className="rv-rs__emit-line">
            <span className="rv-rs__emit-label">emit</span>
            <span className="rv-rs__event-chip">{entry.eventName}</span>
            {entry.attrs && entry.attrs.length > 0 && (
              <span className="rv-rs__emit-attrs">{entry.attrs.join(' · ')}</span>
            )}
            <span className="rv-rs__msg-ts">· {formatShortTime(entry.ts)}</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rv-rs__msg rv-rs__msg--assistant" data-kind="assistant">
      <div className="rv-rs__msg-rail rv-rs__msg-rail--avatar">
        <PersonaAvatar role={personaRole} letter={personaLetter} size={22} />
      </div>
      <div className="rv-rs__msg-body">
        <div className="rv-rs__msg-author">{personaLabel}</div>
        <div className="rv-rs__msg-text">{entry.text}</div>
        <div className="rv-rs__msg-time">{formatShortTime(entry.ts)}</div>
      </div>
    </div>
  );
}

function ActiveCursor({
  personaLabel,
  personaLetter,
  personaRole,
}: {
  personaLabel: string;
  personaLetter: string;
  personaRole: PersonaRole;
}) {
  return (
    <div
      className="rv-rs__msg rv-rs__msg--assistant rv-rs__msg--cursor"
      data-testid="sessions-cursor"
    >
      <div className="rv-rs__msg-rail rv-rs__msg-rail--avatar">
        <PersonaAvatar role={personaRole} letter={personaLetter} size={22} />
      </div>
      <div className="rv-rs__msg-body">
        <div className="rv-rs__msg-author">{personaLabel}</div>
        <div className="rv-rs__thinking">
          <span className="rv-rs__thinking-dot" />
          <span className="rv-rs__thinking-dot" />
          <span className="rv-rs__thinking-dot" />
          <span className="rv-rs__thinking-label">thinking…</span>
        </div>
      </div>
    </div>
  );
}

function Composer({ session }: { session: Session }) {
  const [text, setText] = useState('');
  const isRunning = session.status === 'running';

  if (!isRunning) {
    return (
      <div
        className="rv-rs__composer rv-rs__composer--closed"
        data-testid="sessions-composer-closed"
      >
        <span className="rv-rs__composer-closed-copy">
          session {session.status} · {formatTimelineStamp(session.createdAt)} · read-only
        </span>
        <button type="button" className="rv-rs__action-btn">
          resume in new session
        </button>
      </div>
    );
  }

  return (
    <div className="rv-rs__composer" data-testid="sessions-composer">
      <div className="rv-rs__composer-prefix">you →</div>
      <textarea
        className="rv-rs__composer-input"
        rows={2}
        placeholder="Steer the raven… (shift-enter for newline, enter to send)"
        value={text}
        onChange={(event) => setText(event.target.value)}
        aria-label="Session message composer"
      />
      <div className="rv-rs__composer-actions">
        <span className="rv-rs__composer-hint">/ commands</span>
        <button type="button" className="rv-rs__send-btn" disabled={!text.trim()}>
          send
        </button>
      </div>
    </div>
  );
}

function LiveSessionChat({
  chatEndpoint,
  sessionName,
  socketHistory,
  showInternalMessages,
  onInternalVisibilitySender,
}: {
  chatEndpoint: string;
  sessionName: string;
  socketHistory: boolean;
  showInternalMessages: boolean;
  onInternalVisibilitySender: (sender: ((visible: boolean) => void) | null) => void;
}) {
  const chat = useSkuldChat(chatEndpoint, {
    historyMode: socketHistory ? 'none' : 'session',
  });

  useEffect(() => {
    onInternalVisibilitySender(chat.sendSetInternalVisibility);
    return () => onInternalVisibilitySender(null);
  }, [chat.sendSetInternalVisibility, onInternalVisibilitySender]);

  return (
    <div className="rv-rs__live-chat" data-testid="sessions-live-chat">
      <SessionChat
        className="rv-rs__live-chat-session"
        showToolbar={false}
        messages={chat.messages}
        streamingContent={chat.streamingContent}
        streamingParts={chat.streamingParts}
        streamingModel={chat.streamingModel}
        connected={chat.connected}
        historyLoaded={chat.historyLoaded}
        participants={chat.participants}
        meshEvents={chat.meshEvents}
        agentEvents={chat.agentEvents}
        pendingPermissions={chat.pendingPermissions}
        pendingInputRequests={chat.pendingInputRequests}
        availableCommands={chat.availableCommands}
        capabilities={chat.capabilities}
        chatEndpoint={chatEndpoint}
        sessionName={sessionName}
        showInternalToggle={false}
        internalVisibility={showInternalMessages}
        onSend={chat.sendMessage}
        onSendDirected={chat.sendDirectedMessages}
        onStop={chat.sendInterrupt}
        onPermissionRespond={chat.respondToPermission}
        onInputRespond={chat.respondToInput}
        onSetInternalVisibility={chat.sendSetInternalVisibility}
      />
    </div>
  );
}

function SessionObservabilityPanel({
  session,
  tab,
}: {
  session: Session;
  tab: Exclude<SessionSurfaceTab, 'chat'>;
}) {
  const volundr = useService<IVolundrService>('volundr');

  if (tab === 'trace') {
    return (
      <div className="rv-rs__observability-panel rv-rs__observability-panel--trace">
        <TelemetryTab
          sessionId={session.id}
          session={null}
          runLabel={titleForSession(session)}
          volundr={volundr}
          isRunning={session.status === 'running'}
        />
      </div>
    );
  }

  return (
    <div className="rv-rs__observability-panel">
      <LiveLogsTab sessionId={session.id} volundr={volundr} />
    </div>
  );
}

function SessionSurfaceTabs({
  activeTab,
  onTabChange,
  tabs = SESSION_SURFACE_TABS,
}: {
  activeTab: SessionSurfaceTab;
  onTabChange: (tab: SessionSurfaceTab) => void;
  tabs?: typeof SESSION_SURFACE_TABS;
}) {
  return (
    <div className="rv-rs__surface-tabs" role="tablist" aria-label="Session view">
      {tabs.map((tab) => {
        const Icon = tab.icon;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.id}
            className={cn(
              'rv-rs__surface-tab',
              activeTab === tab.id && 'rv-rs__surface-tab--active',
            )}
            onClick={() => onTabChange(tab.id)}
          >
            <Icon className="rv-rs__surface-tab-icon" />
            <span>{tab.label}</span>
          </button>
        );
      })}
    </div>
  );
}

function ContextSidebar({
  session,
  ravn,
  budget,
  persona,
  entries,
  personaLabel,
}: {
  session: Session;
  ravn: Ravn | null;
  budget: { spentUsd: number; capUsd: number } | undefined;
  persona: PersonaDetail | undefined;
  entries: TranscriptEntry[];
  personaLabel: string;
}) {
  const summary = summarizeSession(session, entries, personaLabel);
  const timeline = deriveTimeline(entries, session);
  const injects = Array.from(
    new Set((persona?.consumes.events ?? []).flatMap((item) => item.injects ?? []).filter(Boolean)),
  );
  const emitted = entries.find((entry) => entry.kind === 'emit');

  return (
    <aside className="rv-rs__aside" aria-label="Session context" data-testid="sessions-context">
      <section className="rv-rs__card" data-testid="sessions-summary">
        <h4 className="rv-rs__card-title">Summary</h4>
        <p className="rv-rs__card-copy">{summary}</p>
      </section>

      <section className="rv-rs__card" data-testid="sessions-timeline">
        <h4 className="rv-rs__card-title">Timeline</h4>
        <ol className="rv-rs__timeline">
          {timeline.map((item) => (
            <li
              key={item.id}
              className={cn('rv-rs__timeline-item', `rv-rs__timeline-item--${item.tone}`)}
            >
              <span className="rv-rs__timeline-dot" />
              <span className="rv-rs__timeline-label">{item.label}</span>
            </li>
          ))}
        </ol>
      </section>

      <section className="rv-rs__card" data-testid="sessions-injects">
        <h4 className="rv-rs__card-title">
          Injects <span className="rv-rs__card-sub">context this session has loaded</span>
        </h4>
        {injects.length > 0 ? (
          <ul className="rv-rs__injects">
            {injects.map((inject) => (
              <li key={inject} className="rv-rs__inject-item">
                <span className="rv-rs__inject-chip">{inject}</span>
                <span className="rv-rs__inject-state">· loaded</span>
              </li>
            ))}
          </ul>
        ) : (
          <div className="rv-rs__empty-mini">no injects configured</div>
        )}
      </section>

      <section className="rv-rs__card" data-testid="sessions-emissions">
        <h4 className="rv-rs__card-title">
          Emissions <span className="rv-rs__card-sub">events this session will produce</span>
        </h4>
        {persona?.produces.eventType ? (
          <div className="rv-rs__emit-card">
            <span className="rv-rs__event-chip rv-rs__event-chip--block">
              {persona.produces.eventType}
            </span>
            <div className="rv-rs__schema">
              {Object.entries(persona.produces.schemaDef).map(([key, value]) => (
                <div key={key}>
                  <span className="rv-rs__schema-key">{key}</span>:{' '}
                  <span className="rv-rs__schema-value">{String(value)}</span>
                </div>
              ))}
            </div>
            <div className="rv-rs__emit-status">
              <span
                className={cn(
                  'rv-rs__emit-status-dot',
                  emitted ? 'rv-rs__emit-status-dot--good' : 'rv-rs__emit-status-dot--warn',
                )}
              />
              {emitted
                ? `emitted · ${formatShortTime(emitted.ts)}`
                : 'pending · will emit on final answer'}
            </div>
          </div>
        ) : (
          <div className="rv-rs__empty-mini">no emission configured</div>
        )}
      </section>

      <section className="rv-rs__card" data-testid="sessions-raven-card">
        <h4 className="rv-rs__card-title">Raven</h4>
        <dl className="rv-rs__defs">
          <dt>name</dt>
          <dd>{ravn?.personaName ?? session.personaName}</dd>
          <dt>location</dt>
          <dd>{normalizeLabel(ravn?.location)}</dd>
          <dt>deploy</dt>
          <dd>{normalizeLabel(ravn?.deployment)}</dd>
          <dt>budget</dt>
          <dd>
            {budget ? `${formatCurrency(budget.spentUsd)} / ${formatCurrency(budget.capUsd)}` : '—'}
          </dd>
        </dl>
      </section>
    </aside>
  );
}

export function SessionsView() {
  const { data: sessions, isLoading, isError, error } = useSessions();
  const { data: ravens } = useRavens();
  const [selectedId, setSelectedId] = useState<string | null>(() => preferredSessionId());
  const [filter, setFilter] = useState<TranscriptFilter>('all');
  const [surfaceTab, setSurfaceTab] = useState<SessionSurfaceTab>('chat');
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [showInternalMessages, setShowInternalMessages] = useState(false);
  const setInternalVisibilityRef = useRef<((visible: boolean) => void) | null>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);

  const sessionList = useMemo(
    () =>
      (sessions ?? []).filter(
        (session) => session.status === 'running' || session.status === 'idle',
      ),
    [sessions],
  );
  const sortedSessions = useMemo(
    () => [...sessionList].sort((left, right) => right.createdAt.localeCompare(left.createdAt)),
    [sessionList],
  );
  const resolvedSelectedId = sortedSessions.length
    ? pickDefaultSession(sortedSessions, selectedId)
    : null;

  useEffect(() => {
    saveStorage(SESSION_STORAGE_KEY, resolvedSelectedId);
  }, [resolvedSelectedId]);

  useEffect(() => {
    const handleSelect = (event: Event) => {
      const detail = (
        event as CustomEvent<string | { sessionId?: string; ravnId?: string; instanceId?: string }>
      ).detail;
      const nextId = typeof detail === 'string' ? detail : detail?.sessionId;
      if (!nextId) return;
      const nextKey =
        typeof detail === 'string' || !detail.ravnId
          ? nextId
          : sessionIdentityKey({
              id: nextId,
              ravnId: detail.ravnId,
              instanceId: detail.instanceId,
            });
      saveStorage(SESSION_STORAGE_KEY, nextKey);
      setSelectedId(nextKey);
    };

    window.addEventListener('ravn:session-selected', handleSelect);
    return () => window.removeEventListener('ravn:session-selected', handleSelect);
  }, []);

  const selectedSession =
    sortedSessions.find((session) => sessionIdentityKey(session) === resolvedSelectedId) ??
    sortedSessions[0] ??
    null;

  const ravnById = useMemo(
    () => new Map((ravens ?? []).map((ravn) => [ravenIdentityKey(ravn.id, ravn.instanceId), ravn])),
    [ravens],
  );

  const selectedRavn = selectedSession
    ? (ravnById.get(ravenIdentityKey(selectedSession.ravnId, selectedSession.instanceId)) ?? null)
    : null;
  const hasLiveChat = Boolean(
    selectedSession?.status === 'running' &&
    normalizeSessionUrl(selectedSession.chatEndpoint ?? null) &&
    (!selectedRavn?.managed || selectedRavn.capabilities?.includes('chat')),
  );

  const {
    data: rawMessages,
    isLoading: messagesLoading,
    isError: messagesError,
  } = useMessages(
    selectedSession?.id ?? '',
    !hasLiveChat,
    selectedSession?.instanceId,
    selectedSession?.ravnId,
  );

  const personaKey = selectedSession
    ? selectedRavn?.kind === 'resident' && !selectedRavn.personaName
      ? ''
      : hasLiveChat
        ? selectedSession.personaName
        : derivePersonaKey(selectedSession)
    : '';
  const { data: persona } = usePersona(personaKey);
  const { data: budget } = useRavnBudget(selectedSession?.ravnId ?? '');

  const personaLabel =
    persona?.name ??
    (personaKey || selectedRavn?.personaName || selectedRavn?.residentName || 'resident');
  const personaRole = persona?.role ?? selectedSession?.personaRole ?? 'build';
  const personaLetter = persona?.letter ?? selectedSession?.personaLetter ?? '?';

  const selectSession = (session: Session) => {
    const key = sessionIdentityKey(session);
    saveStorage(SESSION_STORAGE_KEY, key);
    setSelectedId(key);
    const params = new URLSearchParams(window.location.search);
    params.set('session', session.id);
    params.set('ravn_id', session.ravnId);
    if (session.instanceId) params.set('instance_id', session.instanceId);
    else params.delete('instance_id');
    window.history.replaceState(null, '', `/ravn/sessions?${params.toString()}`);
  };

  const toggleInternalMessages = () => {
    const next = !showInternalMessages;
    setShowInternalMessages(next);
    setInternalVisibilityRef.current?.(next);
  };

  const entries = useMemo(() => {
    if (!selectedSession) return [];
    return buildTranscript(selectedSession, selectedRavn, personaLabel, rawMessages ?? []);
  }, [personaLabel, rawMessages, selectedRavn, selectedSession]);

  const filteredEntries = useMemo(() => filterTranscript(entries, filter), [entries, filter]);

  useEffect(() => {
    const node = transcriptRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [selectedSession?.id, filteredEntries.length]);

  if (isLoading) {
    return (
      <div className="rv-rs__state" data-testid="sessions-loading">
        <LoadingState label="Loading sessions…" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="rv-rs__state" data-testid="sessions-error">
        <ErrorState message={error instanceof Error ? error.message : 'Failed to load sessions'} />
      </div>
    );
  }

  if (!selectedSession) {
    return (
      <div className="rv-rs__state" data-testid="sessions-empty">
        <div className="rv-rs__empty-mini">no sessions yet</div>
      </div>
    );
  }

  const activeSessions = sortedSessions.filter((session) => session.status === 'running');
  const idleSessions = sortedSessions.filter((session) => session.status === 'idle');
  const anchorTime = deriveAnchorTime(sortedSessions);

  // A running session with a Skuld endpoint is a real live chat — the shared
  // SessionChat becomes the transcript. Everything else keeps the read-only view.
  const liveChatEndpoint = hasLiveChat
    ? normalizeSessionUrl(selectedSession.chatEndpoint ?? null)
    : null;
  const surfaceTabs = selectedRavn?.managed
    ? SESSION_SURFACE_TABS.filter((tab) => tab.id === 'chat')
    : SESSION_SURFACE_TABS;
  const resolvedSurfaceTab = surfaceTabs.some((tab) => tab.id === surfaceTab) ? surfaceTab : 'chat';

  return (
    <div className="rv-rs" data-testid="sessions-page">
      <aside
        className={cn('rv-rs__rail', railCollapsed && 'rv-rs__rail--collapsed')}
        aria-label="Sessions"
      >
        {railCollapsed ? (
          <>
            <div className="rv-rs__rail-collapsed-head">
              <button
                type="button"
                onClick={() => setRailCollapsed(false)}
                className="rv-rs__rail-toggle"
                data-testid="sessions-sidebar-toggle"
                aria-label="Expand sessions sidebar"
              >
                ›
              </button>
            </div>
            <CollapsedSessionRail
              sessions={sortedSessions}
              selectedId={sessionIdentityKey(selectedSession)}
              onSelect={selectSession}
            />
          </>
        ) : (
          <>
            <div className="rv-rs__rail-head">
              <div className="rv-rs__rail-head-row">
                <div>
                  <div className="rv-rs__rail-title">Sessions</div>
                  <div className="rv-rs__rail-subtitle">
                    {activeSessions.length} active · {idleSessions.length} idle
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setRailCollapsed(true)}
                  className="rv-rs__rail-toggle"
                  data-testid="sessions-sidebar-toggle"
                  aria-label="Collapse sessions sidebar"
                >
                  ‹
                </button>
              </div>
            </div>

            <div className="rv-rs__rail-body">
              <SessionRailGroup
                label="active"
                count={activeSessions.length}
                sessions={activeSessions}
                selectedId={sessionIdentityKey(selectedSession)}
                anchorTime={anchorTime}
                onSelect={selectSession}
              />
              <SessionRailGroup
                label="idle"
                count={idleSessions.length}
                sessions={idleSessions}
                selectedId={sessionIdentityKey(selectedSession)}
                anchorTime={anchorTime}
                onSelect={selectSession}
              />
            </div>
          </>
        )}
      </aside>

      <main className="rv-rs__main">
        <SessionHeader
          session={selectedSession}
          ravn={selectedRavn}
          personaLabel={personaLabel}
          showInternalMessages={showInternalMessages}
          onToggleInternalMessages={toggleInternalMessages}
        />
        <SessionSurfaceTabs
          activeTab={resolvedSurfaceTab}
          onTabChange={setSurfaceTab}
          tabs={surfaceTabs}
        />
        {resolvedSurfaceTab === 'chat' ? (
          <div className={cn('rv-rs__body', liveChatEndpoint && 'rv-rs__body--chat-only')}>
            <section className="rv-rs__chat">
              {liveChatEndpoint ? (
                <LiveSessionChat
                  chatEndpoint={liveChatEndpoint}
                  sessionName={
                    selectedRavn?.residentName ||
                    selectedRavn?.personaName ||
                    selectedSession.personaName
                  }
                  socketHistory={selectedRavn?.kind === 'resident'}
                  showInternalMessages={showInternalMessages}
                  onInternalVisibilitySender={(sender) => {
                    setInternalVisibilityRef.current = sender;
                  }}
                />
              ) : (
                <>
                  <TranscriptToolbar filter={filter} onFilterChange={setFilter} />
                  <div
                    className="rv-rs__scroll"
                    ref={transcriptRef}
                    role="log"
                    aria-label="Session transcript"
                  >
                    {messagesLoading ? (
                      <div className="rv-rs__empty">loading transcript…</div>
                    ) : messagesError ? (
                      <div className="rv-rs__empty rv-rs__empty--error">
                        failed to load transcript
                      </div>
                    ) : (
                      <>
                        {filteredEntries.map((entry) => (
                          <TranscriptMessage
                            key={entry.id}
                            entry={entry}
                            personaLabel={personaLabel}
                            personaLetter={personaLetter}
                            personaRole={personaRole}
                          />
                        ))}
                        {selectedSession.status === 'running' && (
                          <ActiveCursor
                            personaLabel={personaLabel}
                            personaLetter={personaLetter}
                            personaRole={personaRole}
                          />
                        )}
                      </>
                    )}
                  </div>
                  {!selectedRavn?.managed && <Composer session={selectedSession} />}
                </>
              )}
            </section>

            {!liveChatEndpoint && (
              <ContextSidebar
                session={selectedSession}
                ravn={selectedRavn}
                budget={budget}
                persona={persona}
                entries={entries}
                personaLabel={personaLabel}
              />
            )}
          </div>
        ) : (
          <SessionObservabilityPanel session={selectedSession} tab={resolvedSurfaceTab} />
        )}
      </main>
    </div>
  );
}
