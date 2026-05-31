import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useService } from '@niuulabs/plugin-sdk';
import { getAuthHeaders } from '@niuulabs/query';
import {
  Dialog,
  DialogContent,
  ErrorState,
  LoadingState,
  SessionChat,
  type MeshNotificationEvent,
  cn,
} from '@niuulabs/ui';
import {
  Archive,
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Eye,
  EyeOff,
  ExternalLink,
  FileCode2,
  FileDiff,
  FilePenLine,
  FolderOpen,
  GitCommitHorizontal,
  MessageSquareText,
  Play,
  RotateCcw,
  ScrollText,
  Sparkles,
  Square,
  SquareTerminal,
  Trash2,
} from 'lucide-react';
import type { IVolundrService } from '../ports/IVolundrService';
import type { IFileSystemPort } from '../ports/IFileSystemPort';
import type {
  SessionChronicle,
  SessionFile,
  VolundrAggregatedLog,
  VolundrLogParticipant,
  VolundrSession,
  VolundrSessionTrace,
  VolundrSessionTraceSpan,
  VolundrSessionTraceSummary,
} from '../models/volundr.model';
import { useSessionDetail } from './hooks/useSessionStore';
import { SessionFilesWorkspace } from './SessionFilesWorkspace';
import {
  meshEventsFromTurns,
  participantsFromTurns,
  transformTurns,
  useSkuldChat,
} from './hooks/useSkuldChat';
import { deriveTerminalWsUrl, normalizeSessionUrl, wsUrlToHttpBase } from './liveSessionTransport';
import { PermissionApprovalPanel } from './PermissionApprovalPanel';
import { buildPermissionAutoApprovalRequest } from './permissionAutoApproval';
import { SessionTerminalLive } from './SessionTerminalLive';
import { StructuredLogViewer } from './components/StructuredLogViewer';
import './LiveSessionDetailPage.css';

type SessionTab = 'chat' | 'terminal' | 'diffs' | 'files' | 'chronicles' | 'telemetry' | 'logs';

const ALL_TABS: Array<{ id: SessionTab; label: string; icon: typeof MessageSquareText }> = [
  { id: 'chat', label: 'Chat', icon: MessageSquareText },
  { id: 'terminal', label: 'Terminal', icon: SquareTerminal },
  { id: 'diffs', label: 'Diffs', icon: FileDiff },
  { id: 'files', label: 'Files', icon: FolderOpen },
  { id: 'chronicles', label: 'Chronicle', icon: ScrollText },
  { id: 'telemetry', label: 'Telemetry', icon: Sparkles },
  { id: 'logs', label: 'Logs', icon: FileCode2 },
];

const FIXED_TAB_ORDER: Partial<Record<SessionTab, number>> = {
  diffs: 25,
  chronicles: 50,
  telemetry: 51,
  logs: 60,
};

export function isSessionBooting(status: string | null | undefined): boolean {
  return status === 'starting' || status === 'provisioning';
}

export function formatCount(value: number): string {
  if (!Number.isFinite(value)) return '0';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1)}m`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}k`;
  return `${value}`;
}

export function formatElapsedSince(value?: string | null): string {
  if (!value) return '—';
  const startedAt = new Date(value).getTime();
  if (!Number.isFinite(startedAt)) return '—';
  const delta = Math.max(0, Date.now() - startedAt);
  const minutes = Math.floor(delta / 60_000);
  if (minutes < 1) return '<1m';
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

export function formatCurrencyCents(value?: number): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `$${(value / 100).toFixed(2)}`;
}

export function formatEventTime(value: number): string {
  const minutes = Math.floor(value / 60);
  const seconds = value % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

export function sessionForgeLabel(session: VolundrSession | null | undefined): string {
  return session?.instanceName ?? session?.instanceId ?? session?.hostname ?? 'shared';
}

export function fileChangeCount(
  files?: {
    added: number;
    modified: number;
    deleted: number;
  } | null,
): number | undefined {
  if (!files) return undefined;
  return files.added + files.modified + files.deleted;
}

function WorkflowHumanGateCard({
  title,
  summary,
  reason,
  recommendation,
  onApprove,
  onRequestChanges,
}: {
  title: string;
  summary: string;
  reason?: string;
  recommendation?: string;
  onApprove: (notes: string) => void;
  onRequestChanges: (notes: string) => void;
}) {
  const [notes, setNotes] = useState('');

  return (
    <section className="niuu-m-3 niuu-mb-0 niuu-rounded-xl niuu-border niuu-border-amber-500/35 niuu-bg-amber-500/8 niuu-p-4 niuu-shadow-[0_18px_40px_-24px_rgba(245,158,11,0.45)]">
      <div className="niuu-flex niuu-items-start niuu-gap-3">
        <div className="niuu-mt-0.5 niuu-rounded-full niuu-bg-amber-500/16 niuu-p-2 niuu-text-amber-300">
          <AlertTriangle className="niuu-h-4 niuu-w-4" />
        </div>
        <div className="niuu-flex-1 niuu-space-y-3">
          <div className="niuu-space-y-1">
            <div className="niuu-text-[11px] niuu-font-mono niuu-uppercase niuu-tracking-[0.08em] niuu-text-amber-200/80">
              Human Gate Requested
            </div>
            <h3 className="niuu-text-[15px] niuu-font-semibold niuu-text-text-primary">{title}</h3>
            <p className="niuu-text-sm niuu-leading-6 niuu-text-text-secondary">{summary}</p>
          </div>
          {(reason || recommendation) && (
            <dl className="niuu-grid niuu-gap-2 niuu-rounded-lg niuu-border niuu-border-white/8 niuu-bg-black/10 niuu-p-3">
              {reason ? (
                <div className="niuu-grid niuu-gap-1">
                  <dt className="niuu-text-[11px] niuu-font-mono niuu-uppercase niuu-tracking-[0.08em] niuu-text-text-muted">
                    Reason
                  </dt>
                  <dd className="niuu-text-sm niuu-text-text-secondary">{reason}</dd>
                </div>
              ) : null}
              {recommendation ? (
                <div className="niuu-grid niuu-gap-1">
                  <dt className="niuu-text-[11px] niuu-font-mono niuu-uppercase niuu-tracking-[0.08em] niuu-text-text-muted">
                    Recommendation
                  </dt>
                  <dd className="niuu-text-sm niuu-text-text-secondary">{recommendation}</dd>
                </div>
              ) : null}
            </dl>
          )}
          <div className="niuu-space-y-2">
            <label
              htmlFor="workflow-human-gate-notes"
              className="niuu-text-[11px] niuu-font-mono niuu-uppercase niuu-tracking-[0.08em] niuu-text-text-muted"
            >
              Reply Notes
            </label>
            <textarea
              id="workflow-human-gate-notes"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              rows={4}
              placeholder="Optional context for the gate decision."
              className="niuu-w-full niuu-rounded-lg niuu-border niuu-border-border niuu-bg-bg-primary niuu-px-3 niuu-py-2.5 niuu-text-sm niuu-text-text-primary placeholder:niuu-text-text-muted"
            />
          </div>
          <div className="niuu-flex niuu-flex-wrap niuu-items-center niuu-justify-between niuu-gap-3">
            <p className="niuu-text-xs niuu-text-text-muted">
              This reply resolves the pending workflow gate and resumes the workflow.
            </p>
            <div className="niuu-flex niuu-flex-wrap niuu-gap-2">
              <button
                type="button"
                onClick={() => {
                  onRequestChanges(notes.trim());
                  setNotes('');
                }}
                className="niuu-inline-flex niuu-items-center niuu-gap-2 niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-primary niuu-px-3 niuu-py-2 niuu-text-sm niuu-text-text-primary hover:niuu-bg-bg-secondary"
              >
                <FilePenLine className="niuu-h-4 niuu-w-4" />
                Request changes
              </button>
              <button
                type="button"
                onClick={() => {
                  onApprove(notes.trim());
                  setNotes('');
                }}
                className="niuu-inline-flex niuu-items-center niuu-gap-2 niuu-rounded-md niuu-bg-brand niuu-px-3 niuu-py-2 niuu-text-sm niuu-font-medium niuu-text-bg-primary hover:niuu-opacity-90"
              >
                <Check className="niuu-h-4 niuu-w-4" />
                Approve
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

export function formatDurationMs(value?: number | null): string {
  if (!value || value <= 0) return '0s';
  const totalSeconds = Math.round(value / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, '0')}m`;
  if (minutes > 0) return `${minutes}m ${String(seconds).padStart(2, '0')}s`;
  return `${seconds}s`;
}

export function formatCompactDurationMs(value?: number | null): string {
  if (!value || value <= 0) return '0s';
  if (value < 10_000) return `${(value / 1000).toFixed(1)}s`;
  return formatDurationMs(value);
}

export function formatPercentOfTotal(value: number, total: number): string {
  if (total <= 0) return '0% of total';
  return `${Math.round((value / total) * 100)}% of total`;
}

export function formatTraceStamp(value?: string | null): string {
  if (!value) return '--:--:--Z';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '--:--:--Z';
  return `${date.toISOString().slice(11, 19)}Z`;
}

export function formatSignedDuration(value: number): string {
  if (!Number.isFinite(value) || value === 0) return '±0s';
  const sign = value > 0 ? '+' : '-';
  return `${sign}${formatDurationMs(Math.abs(value))}`;
}

export function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const ordered = [...values].sort((left, right) => left - right);
  const midpoint = Math.floor(ordered.length / 2);
  if (ordered.length % 2 === 1) return ordered[midpoint] ?? null;
  const left = ordered[midpoint - 1] ?? 0;
  const right = ordered[midpoint] ?? 0;
  return Math.round((left + right) / 2);
}

export function percentile(values: number[], quantile: number): number | null {
  if (values.length === 0) return null;
  const ordered = [...values].sort((left, right) => left - right);
  const index = Math.min(ordered.length - 1, Math.max(0, Math.ceil(quantile * ordered.length) - 1));
  return ordered[index] ?? null;
}

export function roundUpDurationMs(value: number, stepMs: number): number {
  if (value <= 0) return stepMs;
  return Math.ceil(value / stepMs) * stepMs;
}

export function deriveComparableSourceKey(session: VolundrSession | null | undefined): string {
  if (!session?.source) return 'unknown';
  if (session.source.type === 'git') {
    return `git:${session.source.repo}:${session.source.branch ?? ''}`;
  }
  const fallbackMountPath = session.source.paths?.[0]?.host_path ?? '';
  return `local:${session.source.local_path ?? session.source.path ?? fallbackMountPath}`;
}

export function looksLikeRunLabel(value: string | null | undefined): boolean {
  if (!value) return false;
  return /^s[-#]/i.test(value) || /^run[-#]/i.test(value);
}

export function pickLongestStage(
  trace: VolundrSessionTrace | null | undefined,
  summary: VolundrSessionTraceSummary | null | undefined,
): VolundrSessionTraceSpan | null {
  const candidates = (trace?.spans ?? [])
    .filter((span) => span.parentSpanId && (span.durationMs ?? 0) > 0)
    .sort((left, right) => (right.durationMs ?? 0) - (left.durationMs ?? 0));
  return candidates[0] ?? summary?.longestSpan ?? null;
}

export function formatStageLabel(span: VolundrSessionTraceSpan | null): string {
  if (!span) return 'n/a';
  if (span.kind === 'session.workflow' || span.kind.startsWith('turn.'))
    return span.name.toLowerCase();
  if (span.kind === 'tool.call') return span.name;
  return span.name || span.kind;
}

export function formatTimelineTick(valueMs: number): string {
  if (valueMs <= 0) return '0ms';
  const totalSeconds = Math.round(valueMs / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (seconds === 0) return `${minutes}m`;
  return `${minutes}m ${String(seconds).padStart(2, '0')}s`;
}

export function formatTimelineHeaderStamp(value?: string | null): string {
  if (!value) return '--:--:--Z';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '--:--:--Z';
  return `${date.toISOString().slice(11, 19)}Z`;
}

export function normalizeTimelineRowLabel(span: VolundrSessionTraceSpan): string {
  if (span.kind === 'session.workflow') return span.name.toLowerCase();
  if (span.kind === 'turn.peer' || span.kind === 'turn.assistant') {
    return (span.actorLabel ?? span.name).toLowerCase();
  }
  if (span.kind === 'turn.user') {
    return span.actorLabel ?? 'input';
  }
  if (span.kind.startsWith('session.')) {
    return span.kind.split('.').slice(1).join(' ') || span.name.toLowerCase();
  }
  return (span.name || span.kind).toLowerCase();
}

export function timelineRowCategory(span: VolundrSessionTraceSpan): string {
  if (span.kind.startsWith('wait.')) return 'wait';
  if (span.kind === 'turn.peer' || span.kind === 'turn.assistant') return 'work';
  if (span.kind === 'turn.user') return 'input';
  if (span.kind === 'session.workflow') return 'workflow';
  return span.actorType ?? 'system';
}

export function timelineRowTone(
  span: VolundrSessionTraceSpan,
): 'active' | 'wait' | 'blocked' | 'system' {
  if (span.status === 'failed' || span.status === 'cancelled') return 'blocked';
  if (span.kind.startsWith('wait.')) return 'wait';
  if (
    span.kind === 'session.workflow' ||
    span.kind.startsWith('turn.') ||
    span.kind === 'tool.call' ||
    span.kind === 'terminal.command'
  ) {
    return 'active';
  }
  return 'system';
}

type TelemetryTimelineRow = {
  id: string;
  span: VolundrSessionTraceSpan;
  label: string;
  category: string;
  startedAt: string;
  endedAt: string | null;
  startOffsetMs: number;
  durationMs: number;
  percentOfTotal: number;
  tone: 'active' | 'wait' | 'blocked' | 'system';
  childSpanCount: number;
  childToneDurations: Record<'active' | 'wait' | 'blocked' | 'system', number>;
  stateDurations: Record<'active' | 'wait' | 'blocked', number>;
  childSegments: Array<{
    id: string;
    tone: 'active' | 'wait' | 'blocked' | 'system';
    startOffsetMs: number;
    durationMs: number;
  }>;
  hatch: boolean;
};

type TelemetrySpanNode = {
  span: VolundrSessionTraceSpan;
  children: TelemetrySpanNode[];
};

type TelemetryBreakdownStage = {
  row: TelemetryTimelineRow;
  node: TelemetrySpanNode;
  descendantCount: number;
};

type TelemetryTurnRow = {
  id: string;
  span: VolundrSessionTraceSpan;
  index: number;
  label: string;
  stageLabel: string;
  durationMs: number;
  startedAt: string;
  endedAt: string | null;
  modelWaitMs: number;
  toolMs: number;
  idleMs: number;
  blockedMs: number;
  toolCallCount: number;
  childSpanCount: number;
  toolNames: string[];
  idleLabel: string | null;
  status: string;
  sourceService: string | null | undefined;
};

type TelemetryToolCategory = 'all' | 'read' | 'edit' | 'write' | 'shell' | 'mcp' | 'other';

type TelemetryToolGroupRow = {
  id: string;
  category: Exclude<TelemetryToolCategory, 'all'>;
  badge: string;
  title: string;
  subtitle: string | null;
  calls: number;
  totalMs: number;
  p50Ms: number;
  p95Ms: number;
  maxMs: number;
  sharePercent: number;
  blockedMs: number;
  blockedCount: number;
};

type TelemetryToolFilter = {
  key: TelemetryToolCategory | string;
  label: string;
  count: number;
  tone: 'neutral' | 'read' | 'edit' | 'write' | 'shell' | 'mcp' | 'other';
};

export function buildTelemetrySpanTree(trace: VolundrSessionTrace): {
  root: TelemetrySpanNode | null;
  nodeById: Map<string, TelemetrySpanNode>;
} {
  const nodeById = new Map<string, TelemetrySpanNode>();
  for (const span of trace.spans) {
    nodeById.set(span.id, { span, children: [] });
  }
  let root: TelemetrySpanNode | null = null;
  for (const span of trace.spans) {
    const node = nodeById.get(span.id);
    if (!node) continue;
    if (!span.parentSpanId) {
      if (!root || span.kind === 'session.lifecycle') root = node;
      continue;
    }
    const parentNode = nodeById.get(span.parentSpanId);
    if (parentNode) {
      parentNode.children.push(node);
    }
  }
  for (const node of nodeById.values()) {
    node.children.sort(
      (left, right) =>
        new Date(left.span.startedAt).getTime() - new Date(right.span.startedAt).getTime(),
    );
  }
  return { root, nodeById };
}

export function spanAttributes(span: VolundrSessionTraceSpan): Record<string, unknown> {
  return span.attributes && typeof span.attributes === 'object' ? span.attributes : {};
}

export function extractTelemetryToolDescriptor(span: VolundrSessionTraceSpan): {
  category: Exclude<TelemetryToolCategory, 'all'>;
  badge: string;
  title: string;
  subtitle: string | null;
} {
  const attrs = spanAttributes(span);
  const command = String(attrs.command ?? '').trim();
  const rawName = String(span.name || '').trim() || 'tool';
  const canonicalName = String(attrs.tool_name ?? attrs.toolName ?? rawName).trim() || rawName;
  const lowerName = canonicalName.toLowerCase();
  const firstToken = canonicalName.split(/\s+/)[0] ?? canonicalName;
  const mcpToken = firstToken.includes('.') ? firstToken : '';
  const tailLabel = mcpToken ? canonicalName.slice(mcpToken.length).trim() : '';

  if (
    span.kind === 'terminal.command' ||
    /^(npm|pnpm|yarn|jest|git|bash|sh)\b/i.test(canonicalName) ||
    /^(npm|pnpm|yarn|jest|git|bash|sh)\b/i.test(command)
  ) {
    return {
      category: 'shell',
      badge: 'shell',
      title: 'shell',
      subtitle: command || canonicalName,
    };
  }

  if (mcpToken) {
    return {
      category: 'mcp',
      badge: 'mcp',
      title: mcpToken.toLowerCase(),
      subtitle: tailLabel ? tailLabel.toLowerCase() : null,
    };
  }

  if (/^(read|review|inspect|search)\b/i.test(lowerName)) {
    return {
      category: 'read',
      badge: 'read',
      title: 'read',
      subtitle: canonicalName.toLowerCase(),
    };
  }

  if (/^(edit|patch|modify|update)\b/i.test(lowerName)) {
    return {
      category: 'edit',
      badge: 'edit',
      title: 'edit',
      subtitle: canonicalName.toLowerCase(),
    };
  }

  if (/^(write|draft|capture|commit|confirm|polish)\b/i.test(lowerName)) {
    return {
      category: 'write',
      badge: 'write',
      title: 'write',
      subtitle: canonicalName.toLowerCase(),
    };
  }

  return {
    category: 'other',
    badge: 'tool',
    title: canonicalName.toLowerCase(),
    subtitle: command || null,
  };
}

export function buildTelemetryToolOverview(trace: VolundrSessionTrace): {
  rows: TelemetryToolGroupRow[];
  primaryFilters: TelemetryToolFilter[];
  totalToolCalls: number;
  totalToolMs: number;
} {
  const toolSpans = trace.spans.filter(
    (span) => span.kind === 'tool.call' || span.kind === 'terminal.command',
  );
  const totalToolMs = toolSpans.reduce((total, span) => total + (span.durationMs ?? 0), 0);
  const groups = new Map<
    string,
    {
      row: Omit<TelemetryToolGroupRow, 'p50Ms' | 'p95Ms' | 'maxMs' | 'sharePercent'>;
      durations: number[];
    }
  >();
  const countsByCategory = new Map<Exclude<TelemetryToolCategory, 'all'>, number>();

  for (const span of toolSpans) {
    const descriptor = extractTelemetryToolDescriptor(span);
    countsByCategory.set(descriptor.category, (countsByCategory.get(descriptor.category) ?? 0) + 1);
    const groupId =
      descriptor.category === 'mcp' || descriptor.category === 'other'
        ? `${descriptor.category}:${descriptor.title}`
        : descriptor.category;
    const durationMs = span.durationMs ?? 0;
    const existing = groups.get(groupId);
    if (existing) {
      existing.row.calls += 1;
      existing.row.totalMs += durationMs;
      existing.row.blockedMs +=
        span.status === 'failed' || span.status === 'cancelled' ? durationMs : 0;
      existing.row.blockedCount += span.status === 'failed' || span.status === 'cancelled' ? 1 : 0;
      existing.durations.push(durationMs);
      if (!existing.row.subtitle && descriptor.subtitle) {
        existing.row.subtitle = descriptor.subtitle;
      }
      continue;
    }

    groups.set(groupId, {
      row: {
        id: groupId,
        category: descriptor.category,
        badge: descriptor.badge,
        title: descriptor.title,
        subtitle: descriptor.subtitle,
        calls: 1,
        totalMs: durationMs,
        blockedMs: span.status === 'failed' || span.status === 'cancelled' ? durationMs : 0,
        blockedCount: span.status === 'failed' || span.status === 'cancelled' ? 1 : 0,
      },
      durations: [durationMs],
    });
  }

  const rows = [...groups.values()]
    .map(({ row, durations }) => ({
      ...row,
      p50Ms: median(durations) ?? 0,
      p95Ms: percentile(durations, 0.95) ?? 0,
      maxMs: durations.reduce((max, value) => Math.max(max, value), 0),
      sharePercent: trace.durationMs > 0 ? Math.round((row.totalMs / trace.durationMs) * 100) : 0,
    }))
    .sort((left, right) => right.totalMs - left.totalMs || right.calls - left.calls);

  const primaryFilters: TelemetryToolFilter[] = [
    {
      key: 'all',
      label: 'all',
      count: toolSpans.length,
      tone: 'neutral',
    },
    ...(['read', 'edit', 'write', 'shell', 'mcp', 'other'] as const)
      .filter((category) => (countsByCategory.get(category) ?? 0) > 0)
      .map((category) => ({
        key: category,
        label: category,
        count: countsByCategory.get(category) ?? 0,
        tone: category,
      })),
  ];

  return {
    rows,
    primaryFilters,
    totalToolCalls: toolSpans.length,
    totalToolMs,
  };
}

export function countTelemetryDescendants(node: TelemetrySpanNode): number {
  return node.children.reduce((total, child) => total + 1 + countTelemetryDescendants(child), 0);
}

export function collectTelemetryDetailSpans(node: TelemetrySpanNode): VolundrSessionTraceSpan[] {
  return node.children.flatMap((child) => {
    if (child.span.kind.startsWith('turn.')) return [];
    return [child.span, ...collectTelemetryDetailSpans(child)];
  });
}

export function nearestTurnAncestorLabel(
  node: TelemetrySpanNode,
  nodeById: Map<string, TelemetrySpanNode>,
): string | null {
  let currentParentId = node.span.parentSpanId;
  while (currentParentId) {
    const parentNode = nodeById.get(currentParentId);
    if (!parentNode) return null;
    if (parentNode.span.kind.startsWith('turn.') || parentNode.span.kind === 'session.workflow') {
      return normalizeTimelineRowLabel(parentNode.span);
    }
    currentParentId = parentNode.span.parentSpanId;
  }
  return null;
}

export function formatTelemetryTaskLabel(span: VolundrSessionTraceSpan): string {
  if (span.kind === 'tool.call') return `tool · ${span.name.toLowerCase()}`;
  if (span.kind === 'terminal.command') return `terminal · ${span.name.toLowerCase()}`;
  if (span.kind.startsWith('wait.')) {
    return `wait · ${(span.name || span.kind.split('.').slice(1).join(' ')).toLowerCase()}`;
  }
  if (span.kind.startsWith('turn.'))
    return `${span.kind.replace('turn.', '')} · ${normalizeTimelineRowLabel(span)}`;
  return `${span.kind.replace('.', ' · ')} · ${normalizeTimelineRowLabel(span)}`;
}

export function buildTelemetryTimelineRows(trace: VolundrSessionTrace): TelemetryTimelineRow[] {
  const rootSpan = trace.spans.find((span) => span.parentSpanId == null);
  if (!rootSpan) return [];
  const rootStart = new Date(rootSpan.startedAt).getTime();
  const totalDuration = trace.durationMs || rootSpan.durationMs || 0;
  const childrenByParent = new Map<string, VolundrSessionTraceSpan[]>();
  for (const span of trace.spans) {
    if (!span.parentSpanId) continue;
    const siblings = childrenByParent.get(span.parentSpanId) ?? [];
    siblings.push(span);
    childrenByParent.set(span.parentSpanId, siblings);
  }
  return trace.spans
    .filter((span) => span.parentSpanId === rootSpan.id)
    .sort((left, right) => new Date(left.startedAt).getTime() - new Date(right.startedAt).getTime())
    .map((span) => {
      const startOffsetMs = Math.max(0, new Date(span.startedAt).getTime() - rootStart);
      const durationMs = span.durationMs ?? 0;
      const percentOfTotal = totalDuration > 0 ? Math.round((durationMs / totalDuration) * 100) : 0;
      const tone = timelineRowTone(span);
      const childSpans = childrenByParent.get(span.id) ?? [];
      const childToneDurations: Record<'active' | 'wait' | 'blocked' | 'system', number> = {
        active: 0,
        wait: 0,
        blocked: 0,
        system: 0,
      };
      const childSegments = childSpans
        .map((childSpan) => {
          const childTone = timelineRowTone(childSpan);
          childToneDurations[childTone] += childSpan.durationMs ?? 0;
          const childStartOffsetMs = Math.max(
            0,
            new Date(childSpan.startedAt).getTime() - new Date(span.startedAt).getTime(),
          );
          const rawChildDurationMs = childSpan.durationMs ?? 0;
          const clampedChildStartOffsetMs = Math.min(childStartOffsetMs, durationMs);
          const clampedChildDurationMs = Math.max(
            0,
            Math.min(clampedChildStartOffsetMs + rawChildDurationMs, durationMs) -
              clampedChildStartOffsetMs,
          );
          return {
            id: childSpan.id,
            tone: childTone,
            startOffsetMs: clampedChildStartOffsetMs,
            durationMs: clampedChildDurationMs,
          };
        })
        .filter((segment) => segment.durationMs > 0)
        .sort((left, right) => left.startOffsetMs - right.startOffsetMs);

      const waitDurationMs =
        tone === 'wait' ? durationMs : Math.min(durationMs, childToneDurations.wait);
      const blockedDurationMs =
        tone === 'blocked'
          ? durationMs
          : Math.min(Math.max(durationMs - waitDurationMs, 0), childToneDurations.blocked);
      const activeDurationMs =
        tone === 'active'
          ? Math.max(durationMs - waitDurationMs - blockedDurationMs, 0)
          : tone === 'blocked' || tone === 'wait'
            ? 0
            : Math.max(durationMs - waitDurationMs - blockedDurationMs, 0);
      return {
        id: span.id,
        span,
        label: normalizeTimelineRowLabel(span),
        category: timelineRowCategory(span),
        startedAt: span.startedAt,
        endedAt: span.endedAt,
        startOffsetMs,
        durationMs,
        percentOfTotal,
        tone,
        childSpanCount: childSpans.length,
        childToneDurations,
        stateDurations: {
          active: activeDurationMs,
          wait: waitDurationMs,
          blocked: blockedDurationMs,
        },
        childSegments,
        hatch: false,
      };
    });
}

export function buildTelemetryTurnRows(trace: VolundrSessionTrace): TelemetryTurnRow[] {
  const { root, nodeById } = buildTelemetrySpanTree(trace);
  const turnNodes = [...nodeById.values()].filter((node) => node.span.kind.startsWith('turn.'));
  if (turnNodes.length === 0) return [];

  const nestedTurnNodes = turnNodes.filter((node) => {
    const parentId = node.span.parentSpanId;
    if (!parentId) return false;
    const parentNode = nodeById.get(parentId);
    return Boolean(parentNode?.span.kind.startsWith('turn.'));
  });

  const directTurnNodes = turnNodes.filter((node) => node.span.parentSpanId === root?.span.id);
  const candidateNodes =
    nestedTurnNodes.length > 0
      ? nestedTurnNodes
      : directTurnNodes.length > 0
        ? directTurnNodes
        : turnNodes;

  return candidateNodes
    .sort(
      (left, right) =>
        new Date(left.span.startedAt).getTime() - new Date(right.span.startedAt).getTime(),
    )
    .map((node, index) => {
      const details = collectTelemetryDetailSpans(node);
      const durationMs = node.span.durationMs ?? 0;
      const toolSpans = details.filter(
        (span) => span.kind === 'tool.call' || span.kind === 'terminal.command',
      );
      const idleSpans = details.filter((span) => span.kind.startsWith('wait.'));
      const blockedSpans = details.filter(
        (span) => span.status === 'failed' || span.status === 'cancelled',
      );
      const toolMs = toolSpans.reduce((total, span) => total + (span.durationMs ?? 0), 0);
      const idleMs = idleSpans.reduce((total, span) => total + (span.durationMs ?? 0), 0);
      const blockedMs = blockedSpans.reduce((total, span) => total + (span.durationMs ?? 0), 0);
      const modelWaitMs = Math.max(durationMs - toolMs - idleMs, 0);
      return {
        id: node.span.id,
        span: node.span,
        index,
        label: `turn #${index + 1}`,
        stageLabel:
          nearestTurnAncestorLabel(node, nodeById) ??
          node.span.actorLabel ??
          normalizeTimelineRowLabel(node.span),
        durationMs,
        startedAt: node.span.startedAt,
        endedAt: node.span.endedAt,
        modelWaitMs,
        toolMs,
        idleMs,
        blockedMs,
        toolCallCount: toolSpans.length,
        childSpanCount: details.length,
        toolNames: toolSpans.map((span) => span.name),
        idleLabel: idleSpans[0]?.name ?? null,
        status: node.span.status,
        sourceService: node.span.sourceService,
      };
    });
}

export function formatTurnDurationTick(valueMs: number): string {
  if (valueMs <= 0) return '0ms';
  return formatDurationMs(valueMs);
}

function TurnMetricLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="niuu-live-telemetry__turn-detail-line">
      <span className="niuu-live-telemetry__turn-detail-key">{label}</span>
      <span className="niuu-live-telemetry__turn-detail-value">{value}</span>
    </div>
  );
}

function TelemetryTurnInspector({
  turns,
  selectedTurnId,
  onSelectTurn,
}: {
  turns: TelemetryTurnRow[];
  selectedTurnId: string | null;
  onSelectTurn: (turnId: string | null) => void;
}) {
  const selectedTurn = turns.find((turn) => turn.id === selectedTurnId) ?? null;
  if (!selectedTurn) {
    return (
      <aside className="niuu-live-telemetry__turn-side" data-testid="telemetry-turn-list">
        <div className="niuu-live-telemetry__turn-side-title">All turns</div>
        <div className="niuu-live-telemetry__turn-list">
          {turns.map((turn) => (
            <button
              key={turn.id}
              type="button"
              className="niuu-live-telemetry__turn-list-item"
              onClick={() => onSelectTurn(turn.id)}
            >
              <span className="niuu-live-telemetry__turn-list-item-main">
                <span className="niuu-live-telemetry__turn-list-item-label">{turn.label}</span>
                <span className="niuu-live-telemetry__turn-list-item-meta">{turn.stageLabel}</span>
              </span>
              <span className="niuu-live-telemetry__turn-list-item-duration">
                {formatDurationMs(turn.durationMs)}
              </span>
            </button>
          ))}
        </div>
      </aside>
    );
  }

  return (
    <aside className="niuu-live-telemetry__turn-side" data-testid="telemetry-turn-detail">
      <div className="niuu-live-telemetry__turn-side-title">{selectedTurn.label}</div>
      <div className="niuu-live-telemetry__turn-detail-lines">
        <TurnMetricLine label="total" value={formatDurationMs(selectedTurn.durationMs)} />
        <TurnMetricLine label="model wait" value={formatDurationMs(selectedTurn.modelWaitMs)} />
        <TurnMetricLine
          label="tool time"
          value={`${formatCompactDurationMs(selectedTurn.toolMs)} • ${selectedTurn.toolCallCount} call${selectedTurn.toolCallCount === 1 ? '' : 's'}`}
        />
        <TurnMetricLine
          label="operator idle"
          value={
            selectedTurn.idleLabel
              ? `${formatCompactDurationMs(selectedTurn.idleMs)} • ${selectedTurn.idleLabel.toLowerCase()}`
              : formatCompactDurationMs(selectedTurn.idleMs)
          }
        />
        <TurnMetricLine
          label="blocked"
          value={
            selectedTurn.blockedMs > 0 ? formatCompactDurationMs(selectedTurn.blockedMs) : '0s'
          }
        />
        <TurnMetricLine label="stage" value={selectedTurn.stageLabel} />
        <TurnMetricLine
          label="span"
          value={`${formatTraceStamp(selectedTurn.startedAt)} → ${formatTraceStamp(selectedTurn.endedAt)}`}
        />
        <TurnMetricLine
          label="source"
          value={
            selectedTurn.sourceService
              ? `${selectedTurn.status} • ${selectedTurn.sourceService}`
              : selectedTurn.status
          }
        />
        <TurnMetricLine label="child spans" value={`${selectedTurn.childSpanCount}`} />
      </div>
      <div className="niuu-live-telemetry__turn-tool-row">
        <span className="niuu-live-telemetry__turn-detail-key">tools</span>
        <div className="niuu-live-telemetry__turn-tool-chips">
          {selectedTurn.toolNames.length > 0 ? (
            selectedTurn.toolNames.slice(0, 4).map((toolName) => (
              <span key={toolName} className="niuu-live-telemetry__turn-tool-chip">
                {toolName}
              </span>
            ))
          ) : (
            <span className="niuu-live-telemetry__turn-tool-chip">none</span>
          )}
        </div>
      </div>
      <button
        type="button"
        className="niuu-live-telemetry__turn-reset"
        onClick={() => onSelectTurn(null)}
      >
        → show all turns
      </button>
    </aside>
  );
}

function TelemetryTurnByTurn({ trace }: { trace: VolundrSessionTrace }) {
  const turns = useMemo(() => buildTelemetryTurnRows(trace), [trace]);
  const [selectedTurnIdState, setSelectedTurnId] = useState<string | null>(null);
  const selectedTurnId =
    turns.find((turn) => turn.id === selectedTurnIdState)?.id ?? null;

  const medianMs = useMemo(() => median(turns.map((turn) => turn.durationMs)), [turns]);
  const p95Ms = useMemo(
    () =>
      percentile(
        turns.map((turn) => turn.durationMs),
        0.95,
      ),
    [turns],
  );
  const maxMs = useMemo(
    () => turns.reduce((currentMax, turn) => Math.max(currentMax, turn.durationMs), 0),
    [turns],
  );
  const axisCeilingMs = Math.max(roundUpDurationMs(maxMs, 15_000), 60_000);
  const ticks = [axisCeilingMs, axisCeilingMs * 0.75, axisCeilingMs * 0.5, axisCeilingMs * 0.25, 0];

  if (turns.length === 0) return null;

  return (
    <section className="niuu-live-telemetry__turn-shell" data-testid="telemetry-turn-shell">
      <div className="niuu-live-telemetry__turn-head">
        <div className="niuu-live-telemetry__turn-head-title">
          <span className="niuu-live-telemetry__turn-head-title-label">Turn-by-turn timing</span>
          <span className="niuu-live-telemetry__turn-head-summary">
            {turns.length} turns
            <span aria-hidden="true">&middot;</span>
            median {formatCompactDurationMs(medianMs)}
            <span aria-hidden="true">&middot;</span>
            p95 {formatDurationMs(p95Ms)}
            <span aria-hidden="true">&middot;</span>
            max {formatDurationMs(maxMs)}
          </span>
        </div>
        <div className="niuu-live-telemetry__turn-legend">
          <span className="niuu-live-telemetry__turn-legend-item">
            <span className="niuu-live-telemetry__turn-legend-swatch niuu-live-telemetry__turn-legend-swatch--model" />
            model wait
          </span>
          <span className="niuu-live-telemetry__turn-legend-item">
            <span className="niuu-live-telemetry__turn-legend-swatch niuu-live-telemetry__turn-legend-swatch--tool" />
            tool
          </span>
          <span className="niuu-live-telemetry__turn-legend-item">
            <span className="niuu-live-telemetry__turn-legend-swatch niuu-live-telemetry__turn-legend-swatch--idle" />
            operator idle
          </span>
        </div>
      </div>

      <div className="niuu-live-telemetry__turn-grid">
        <div className="niuu-live-telemetry__turn-chart">
          <div className="niuu-live-telemetry__turn-axis">
            {ticks.map((tick) => (
              <div key={tick} className="niuu-live-telemetry__turn-axis-label">
                {formatTurnDurationTick(tick)}
              </div>
            ))}
          </div>
          <div className="niuu-live-telemetry__turn-plot">
            {ticks.map((tick) => {
              const bottomPct = axisCeilingMs > 0 ? (tick / axisCeilingMs) * 100 : 0;
              return (
                <div
                  key={`grid-${tick}`}
                  className="niuu-live-telemetry__turn-grid-line"
                  style={{ bottom: `${bottomPct}%` }}
                />
              );
            })}
            {[
              { key: 'p95', value: p95Ms },
              { key: 'p50', value: medianMs },
            ]
              .filter((line): line is { key: 'p95' | 'p50'; value: number } => Boolean(line.value))
              .map((line) => {
                const bottomPct = axisCeilingMs > 0 ? ((line.value ?? 0) / axisCeilingMs) * 100 : 0;
                return (
                  <div
                    key={line.key}
                    className={cn(
                      'niuu-live-telemetry__turn-stat-line',
                      line.key === 'p95'
                        ? 'niuu-live-telemetry__turn-stat-line--p95'
                        : 'niuu-live-telemetry__turn-stat-line--p50',
                    )}
                    style={{ bottom: `${bottomPct}%` }}
                  >
                    <span className="niuu-live-telemetry__turn-stat-label">
                      {line.key} {formatDurationMs(line.value)}
                    </span>
                  </div>
                );
              })}
            <div className="niuu-live-telemetry__turn-bars">
              {turns.map((turn) => {
                const totalHeight = axisCeilingMs > 0 ? (turn.durationMs / axisCeilingMs) * 100 : 0;
                const selected = selectedTurnId === turn.id;
                return (
                  <button
                    key={turn.id}
                    type="button"
                    className={cn(
                      'niuu-live-telemetry__turn-bar-col',
                      selected && 'niuu-live-telemetry__turn-bar-col--selected',
                    )}
                    onClick={() =>
                      setSelectedTurnId((current) => (current === turn.id ? null : turn.id))
                    }
                    data-testid={`telemetry-turn-bar-${turn.index + 1}`}
                  >
                    <div className="niuu-live-telemetry__turn-bar-track">
                      <div
                        className="niuu-live-telemetry__turn-bar-stack"
                        style={{ height: `${Math.max(totalHeight, 6)}%` }}
                      >
                        {turn.idleMs > 0 ? (
                          <span
                            className="niuu-live-telemetry__turn-bar-segment niuu-live-telemetry__turn-bar-segment--idle"
                            style={{
                              height: `${Math.max((turn.idleMs / turn.durationMs) * 100, 4)}%`,
                            }}
                          />
                        ) : null}
                        {turn.toolMs > 0 ? (
                          <span
                            className="niuu-live-telemetry__turn-bar-segment niuu-live-telemetry__turn-bar-segment--tool"
                            style={{
                              height: `${Math.max((turn.toolMs / turn.durationMs) * 100, 4)}%`,
                            }}
                          />
                        ) : null}
                        {turn.modelWaitMs > 0 ? (
                          <span
                            className="niuu-live-telemetry__turn-bar-segment niuu-live-telemetry__turn-bar-segment--model"
                            style={{
                              height: `${Math.max((turn.modelWaitMs / turn.durationMs) * 100, 4)}%`,
                            }}
                          />
                        ) : null}
                      </div>
                    </div>
                    <span className="niuu-live-telemetry__turn-bar-label">{turn.index + 1}</span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        <TelemetryTurnInspector
          turns={turns}
          selectedTurnId={selectedTurnId}
          onSelectTurn={setSelectedTurnId}
        />
      </div>
    </section>
  );
}

function TelemetryTimeline({
  trace,
  selectedRowId,
  onSelectRow,
}: {
  trace: VolundrSessionTrace;
  selectedRowId: string | null;
  onSelectRow: (rowId: string) => void;
}) {
  const totalDurationMs = trace.durationMs || 0;
  const rows = useMemo(() => buildTelemetryTimelineRows(trace), [trace]);
  const [hoveredRowId, setHoveredRowId] = useState<string | null>(null);
  const markers = useMemo(() => {
    if (totalDurationMs <= 0) return [0];
    return [0, totalDurationMs / 3, (totalDurationMs * 2) / 3, totalDurationMs].map((value) =>
      Math.round(value),
    );
  }, [totalDurationMs]);

  if (rows.length === 0) {
    return null;
  }

  return (
    <section className="niuu-live-telemetry__timeline-shell">
      <div className="niuu-live-telemetry__timeline-head">
        <div className="niuu-live-telemetry__timeline-title">
          <span className="niuu-live-telemetry__timeline-title-label">Timeline</span>
          <span>{formatTimelineHeaderStamp(trace.startedAt)}</span>
          <span aria-hidden="true">→</span>
          <span>{formatTimelineHeaderStamp(trace.endedAt)}</span>
          <span aria-hidden="true">•</span>
          <span>{formatDurationMs(totalDurationMs)}</span>
        </div>
        <div className="niuu-live-telemetry__timeline-hint">
          click stage to focus · hover for exact span
        </div>
      </div>

      <div className="niuu-live-telemetry__timeline-grid">
        <div className="niuu-live-telemetry__timeline-axis">
          {markers.map((marker, index) => {
            const left = totalDurationMs > 0 ? (marker / totalDurationMs) * 100 : 0;
            const isLast = index === markers.length - 1;
            return (
              <div
                key={marker}
                className={cn(
                  'niuu-live-telemetry__timeline-axis-marker',
                  isLast && 'niuu-live-telemetry__timeline-axis-marker--end',
                )}
                style={{ left: `${left}%` }}
              >
                <span>{formatTimelineTick(marker)}</span>
              </div>
            );
          })}
        </div>
        <div className="niuu-live-telemetry__timeline-body">
          <div className="niuu-live-telemetry__timeline-guides" aria-hidden="true">
            {markers.map((marker) => {
              const left = totalDurationMs > 0 ? (marker / totalDurationMs) * 100 : 0;
              return (
                <div
                  key={`guide-${marker}`}
                  className="niuu-live-telemetry__timeline-guide"
                  style={{ left: `${left}%` }}
                />
              );
            })}
          </div>

          {rows.map((row) => {
            const left = totalDurationMs > 0 ? (row.startOffsetMs / totalDurationMs) * 100 : 0;
            const width = totalDurationMs > 0 ? (row.durationMs / totalDurationMs) * 100 : 0;
            const visibleWidth = row.durationMs > 0 ? Math.max(width, 1.4) : 1.2;
            const labelInsideBar = visibleWidth > 18;
            const hoverDetails = [
              {
                key: 'active',
                label: 'active',
                value: row.stateDurations.active,
              },
              {
                key: 'wait',
                label: 'wait',
                value: row.stateDurations.wait,
              },
              {
                key: 'blocked',
                label: 'blocked',
                value: row.stateDurations.blocked,
              },
            ] as const;
            const tooltipAlign = left > 68 ? 'end' : 'start';
            const showTooltip = hoveredRowId === row.id;
            return (
              <div
                key={row.id}
                className={cn(
                  'niuu-live-telemetry__timeline-row',
                  selectedRowId === row.id && 'niuu-live-telemetry__timeline-row--selected',
                )}
              >
                <div className="niuu-live-telemetry__timeline-label">
                  <span
                    className={cn(
                      'niuu-live-telemetry__timeline-label-dot',
                      `niuu-live-telemetry__timeline-label-dot--${row.tone}`,
                    )}
                  />
                  <span className="niuu-live-telemetry__timeline-label-title">{row.label}</span>
                  <span className="niuu-live-telemetry__timeline-label-meta">{row.category}</span>
                </div>
                <div className="niuu-live-telemetry__timeline-track">
                  <div
                    className={cn(
                      'niuu-live-telemetry__timeline-bar',
                      (showTooltip || selectedRowId === row.id) &&
                        'niuu-live-telemetry__timeline-bar--hovered',
                      `niuu-live-telemetry__timeline-bar--${row.tone}`,
                      row.hatch && 'niuu-live-telemetry__timeline-bar--hatched',
                    )}
                    style={{ left: `${left}%`, width: `${visibleWidth}%` }}
                    title={`${row.label} · ${formatDurationMs(row.durationMs)} · ${row.percentOfTotal}%`}
                    onMouseEnter={() => setHoveredRowId(row.id)}
                    onMouseLeave={() =>
                      setHoveredRowId((current) => (current === row.id ? null : current))
                    }
                    onFocus={() => setHoveredRowId(row.id)}
                    onBlur={() =>
                      setHoveredRowId((current) => (current === row.id ? null : current))
                    }
                    onClick={() => onSelectRow(row.id)}
                    tabIndex={0}
                    role="button"
                    aria-label={`${row.label} trace details`}
                    aria-pressed={selectedRowId === row.id}
                  >
                    {row.childSegments
                      .filter((segment) => segment.tone === 'wait' || segment.tone === 'blocked')
                      .map((segment) => {
                        const segmentLeft =
                          row.durationMs > 0 ? (segment.startOffsetMs / row.durationMs) * 100 : 0;
                        const segmentWidth =
                          row.durationMs > 0
                            ? Math.max((segment.durationMs / row.durationMs) * 100, 1.1)
                            : 1.1;
                        return (
                          <span
                            key={segment.id}
                            className={cn(
                              'niuu-live-telemetry__timeline-bar-segment',
                              `niuu-live-telemetry__timeline-bar-segment--${segment.tone}`,
                            )}
                            style={{ left: `${segmentLeft}%`, width: `${segmentWidth}%` }}
                            data-testid={`telemetry-row-segment-${row.id}-${segment.tone}`}
                            aria-hidden="true"
                          />
                        );
                      })}
                    {showTooltip ? (
                      <div
                        className={cn(
                          'niuu-live-telemetry__timeline-tooltip',
                          tooltipAlign === 'end'
                            ? 'niuu-live-telemetry__timeline-tooltip--end'
                            : 'niuu-live-telemetry__timeline-tooltip--start',
                        )}
                        data-testid={`telemetry-tooltip-${row.id}`}
                      >
                        <div className="niuu-live-telemetry__timeline-tooltip-title-row">
                          <span className="niuu-live-telemetry__timeline-tooltip-title">
                            {row.label}
                          </span>
                          <span className="niuu-live-telemetry__timeline-tooltip-meta">
                            {row.category}
                          </span>
                        </div>
                        <div className="niuu-live-telemetry__timeline-tooltip-line">
                          <span className="niuu-live-telemetry__timeline-tooltip-key">span</span>
                          <span className="niuu-live-telemetry__timeline-tooltip-value">
                            {formatTraceStamp(row.startedAt)}
                            <span aria-hidden="true">→</span>
                            {formatTraceStamp(row.endedAt)}
                          </span>
                        </div>
                        <div className="niuu-live-telemetry__timeline-tooltip-line">
                          <span className="niuu-live-telemetry__timeline-tooltip-key">
                            duration
                          </span>
                          <span className="niuu-live-telemetry__timeline-tooltip-value">
                            {formatDurationMs(row.durationMs)}
                            <span aria-hidden="true">•</span>
                            {row.percentOfTotal}% of total
                          </span>
                        </div>
                        <div className="niuu-live-telemetry__timeline-tooltip-line">
                          <span className="niuu-live-telemetry__timeline-tooltip-key">status</span>
                          <span className="niuu-live-telemetry__timeline-tooltip-value">
                            {row.span.status}
                            {row.span.sourceService ? (
                              <>
                                <span aria-hidden="true">•</span>
                                {row.span.sourceService}
                              </>
                            ) : null}
                          </span>
                        </div>
                        <div className="niuu-live-telemetry__timeline-tooltip-line">
                          <span className="niuu-live-telemetry__timeline-tooltip-key">
                            child spans
                          </span>
                          <span className="niuu-live-telemetry__timeline-tooltip-value">
                            {row.childSpanCount}
                          </span>
                        </div>
                        {hoverDetails.length > 0 ? (
                          <div className="niuu-live-telemetry__timeline-tooltip-breakdown">
                            {hoverDetails.map((detail) => (
                              <span
                                key={detail.key}
                                className="niuu-live-telemetry__timeline-tooltip-breakdown-item"
                              >
                                <span
                                  className={cn(
                                    'niuu-live-telemetry__timeline-tooltip-breakdown-swatch',
                                    `niuu-live-telemetry__timeline-tooltip-breakdown-swatch--${detail.key}`,
                                  )}
                                />
                                {detail.label} {formatDurationMs(detail.value)}
                              </span>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                    {labelInsideBar ? (
                      <span className="niuu-live-telemetry__timeline-bar-copy">
                        {`${row.label} · ${formatDurationMs(row.durationMs)} · ${row.percentOfTotal}%`}
                      </span>
                    ) : null}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function TelemetryStateStack({
  activeMs,
  waitMs,
  blockedMs,
  totalMs,
  compact = false,
}: {
  activeMs: number;
  waitMs: number;
  blockedMs: number;
  totalMs: number;
  compact?: boolean;
}) {
  const safeTotal = Math.max(totalMs, 1);
  const segments = [
    { key: 'active', value: activeMs },
    { key: 'wait', value: waitMs },
    { key: 'blocked', value: blockedMs },
  ] as const;
  return (
    <div
      className={cn(
        'niuu-live-telemetry__state-stack',
        compact && 'niuu-live-telemetry__state-stack--compact',
      )}
    >
      <div className="niuu-live-telemetry__state-stack-track">
        {segments
          .filter((segment) => segment.value > 0)
          .map((segment) => (
            <span
              key={segment.key}
              className={cn(
                'niuu-live-telemetry__state-stack-segment',
                `niuu-live-telemetry__state-stack-segment--${segment.key}`,
              )}
              style={{ width: `${Math.max((segment.value / safeTotal) * 100, 1)}%` }}
            />
          ))}
      </div>
      {!compact ? (
        <div className="niuu-live-telemetry__state-stack-meta">
          <span>{formatCompactDurationMs(activeMs)}</span>
          <span>&middot;</span>
          <span>{formatCompactDurationMs(waitMs)}</span>
          <span>&middot;</span>
          <span>{formatCompactDurationMs(blockedMs)}</span>
        </div>
      ) : null}
    </div>
  );
}

function TelemetryTaskRow({ node, depth }: { node: TelemetrySpanNode; depth: number }) {
  const tone = timelineRowTone(node.span);
  const durationMs = node.span.durationMs ?? 0;
  return (
    <>
      <div className="niuu-live-telemetry__breakdown-task-row">
        <div className="niuu-live-telemetry__breakdown-task-stage">
          <span
            className="niuu-live-telemetry__breakdown-task-indent"
            style={{ width: `${depth * 18}px` }}
            aria-hidden="true"
          />
          <span className="niuu-live-telemetry__breakdown-task-branch" aria-hidden="true" />
          <span className="niuu-live-telemetry__breakdown-task-label">
            {formatTelemetryTaskLabel(node.span)}
          </span>
        </div>
        <div className="niuu-live-telemetry__breakdown-task-duration">
          {formatCompactDurationMs(durationMs)}
        </div>
        <div className="niuu-live-telemetry__breakdown-task-visual">
          <TelemetryStateStack
            activeMs={tone === 'active' ? durationMs : 0}
            waitMs={tone === 'wait' ? durationMs : 0}
            blockedMs={tone === 'blocked' ? durationMs : 0}
            totalMs={durationMs}
            compact
          />
          {tone === 'blocked' ? (
            <span className="niuu-live-telemetry__breakdown-task-warning">
              blocked {formatCompactDurationMs(durationMs)}
            </span>
          ) : null}
        </div>
      </div>
      {node.children.map((child) => (
        <TelemetryTaskRow key={child.span.id} node={child} depth={depth + 1} />
      ))}
    </>
  );
}

function TelemetryStageBreakdown({
  trace,
  rows,
  selectedRowId,
  expandedRowIds,
  onSelectRow,
  onToggleRow,
}: {
  trace: VolundrSessionTrace;
  rows: TelemetryTimelineRow[];
  selectedRowId: string | null;
  expandedRowIds: Set<string>;
  onSelectRow: (rowId: string) => void;
  onToggleRow: (rowId: string) => void;
}) {
  const { nodeById } = useMemo(() => buildTelemetrySpanTree(trace), [trace]);
  const stages = useMemo<TelemetryBreakdownStage[]>(
    () =>
      rows
        .map((row) => {
          const node = nodeById.get(row.id);
          if (!node) return null;
          return {
            row,
            node,
            descendantCount: countTelemetryDescendants(node),
          };
        })
        .filter((stage): stage is TelemetryBreakdownStage => Boolean(stage)),
    [nodeById, rows],
  );
  const taskCount = stages.reduce((total, stage) => total + stage.descendantCount, 0);

  return (
    <section className="niuu-live-telemetry__breakdown-shell" data-testid="telemetry-breakdown">
      <div className="niuu-live-telemetry__breakdown-head">
        <div className="niuu-live-telemetry__breakdown-title-row">
          <span className="niuu-live-telemetry__breakdown-title">Stage breakdown</span>
          <span className="niuu-live-telemetry__breakdown-summary">
            {stages.length} stages
            <span aria-hidden="true">&middot;</span>
            {taskCount} tasks
          </span>
        </div>
        <div className="niuu-live-telemetry__breakdown-columns">
          <span>Stage</span>
          <span>Duration</span>
          <span>% total</span>
          <span>Active / Wait / Blocked</span>
          <span>Vs median</span>
        </div>
      </div>

      <div className="niuu-live-telemetry__breakdown-list">
        {stages.map((stage) => {
          const isExpanded = expandedRowIds.has(stage.row.id);
          const isSelected = selectedRowId === stage.row.id;
          const ChevronIcon = isExpanded ? ChevronDown : ChevronRight;
          return (
            <div
              key={stage.row.id}
              className={cn(
                'niuu-live-telemetry__breakdown-stage',
                isSelected && 'niuu-live-telemetry__breakdown-stage--selected',
              )}
            >
              <button
                type="button"
                className="niuu-live-telemetry__breakdown-stage-row"
                onClick={() => onSelectRow(stage.row.id)}
              >
                <div className="niuu-live-telemetry__breakdown-stage-cell">
                  <span
                    className="niuu-live-telemetry__breakdown-chevron"
                    onClick={(event) => {
                      event.stopPropagation();
                      onToggleRow(stage.row.id);
                    }}
                  >
                    <ChevronIcon className="niuu-live-telemetry__breakdown-chevron-icon" />
                  </span>
                  <span
                    className={cn(
                      'niuu-live-telemetry__breakdown-dot',
                      `niuu-live-telemetry__breakdown-dot--${stage.row.tone}`,
                    )}
                  />
                  <span className="niuu-live-telemetry__breakdown-stage-label">
                    {stage.row.label}
                  </span>
                  <span className="niuu-live-telemetry__breakdown-stage-meta">
                    {stage.row.category}
                  </span>
                </div>
                <div className="niuu-live-telemetry__breakdown-duration">
                  {formatDurationMs(stage.row.durationMs)}
                </div>
                <div className="niuu-live-telemetry__breakdown-percent">
                  <div className="niuu-live-telemetry__breakdown-percent-track">
                    <span
                      className="niuu-live-telemetry__breakdown-percent-fill"
                      style={{ width: `${Math.max(stage.row.percentOfTotal, 2)}%` }}
                    />
                  </div>
                  <span>{stage.row.percentOfTotal}%</span>
                </div>
                <div className="niuu-live-telemetry__breakdown-state">
                  <TelemetryStateStack
                    activeMs={stage.row.stateDurations.active}
                    waitMs={stage.row.stateDurations.wait}
                    blockedMs={stage.row.stateDurations.blocked}
                    totalMs={stage.row.durationMs}
                  />
                </div>
                <div className="niuu-live-telemetry__breakdown-delta">
                  <span className="niuu-live-telemetry__breakdown-delta-pill">&middot; --</span>
                </div>
              </button>

              {isExpanded && stage.node.children.length > 0 ? (
                <div className="niuu-live-telemetry__breakdown-stage-children">
                  {stage.node.children.map((child) => (
                    <TelemetryTaskRow key={child.span.id} node={child} depth={1} />
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}

        <div className="niuu-live-telemetry__breakdown-total-row">
          <div className="niuu-live-telemetry__breakdown-total-label">Total</div>
          <div className="niuu-live-telemetry__breakdown-total-duration">
            {formatDurationMs(trace.durationMs)}
          </div>
          <div className="niuu-live-telemetry__breakdown-total-percent">100%</div>
          <div className="niuu-live-telemetry__breakdown-total-state">
            <TelemetryStateStack
              activeMs={rows.reduce((total, row) => total + row.stateDurations.active, 0)}
              waitMs={rows.reduce((total, row) => total + row.stateDurations.wait, 0)}
              blockedMs={rows.reduce((total, row) => total + row.stateDurations.blocked, 0)}
              totalMs={trace.durationMs}
            />
          </div>
          <div className="niuu-live-telemetry__breakdown-total-delta">
            <span className="niuu-live-telemetry__delta-pill niuu-live-telemetry__delta-pill--slower">
              + --
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}

function TelemetryToolCallsOverview({ trace }: { trace: VolundrSessionTrace }) {
  const overview = useMemo(() => buildTelemetryToolOverview(trace), [trace]);
  const [selectedCategory, setSelectedCategory] = useState<TelemetryToolCategory>('all');
  const [selectedGroupIdState, setSelectedGroupId] = useState<string>('all');
  const selectedGroupId =
    selectedCategory === 'all'
      ? 'all'
      : overview.rows.some(
            (row) => row.category === selectedCategory && row.id === selectedGroupIdState,
          )
        ? selectedGroupIdState
        : 'all';

  const visibleRows = useMemo(() => {
    const categoryRows =
      selectedCategory === 'all'
        ? overview.rows
        : overview.rows.filter((row) => row.category === selectedCategory);
    return selectedGroupId === 'all'
      ? categoryRows
      : categoryRows.filter((row) => row.id === selectedGroupId);
  }, [overview.rows, selectedCategory, selectedGroupId]);

  const secondaryFilters = useMemo(() => {
    const categoryRows =
      selectedCategory === 'all'
        ? []
        : overview.rows.filter((row) => row.category === selectedCategory);
    if (categoryRows.length <= 1) return [];
    return [
      {
        key: 'all',
        label: `all ${selectedCategory}`,
        count: categoryRows.reduce((total, row) => total + row.calls, 0),
        tone: selectedCategory === 'all' ? 'neutral' : selectedCategory,
      },
      ...categoryRows.map((row) => ({
        key: row.id,
        label: row.title,
        count: row.calls,
        tone: row.category,
      })),
    ];
  }, [overview.rows, selectedCategory]);

  if (overview.totalToolCalls === 0) {
    return (
      <section className="niuu-live-telemetry__tools-shell">
        <div className="niuu-live-telemetry__tools-head">
          <div className="niuu-live-telemetry__tools-title-row">
            <span className="niuu-live-telemetry__tools-title">Tool calls</span>
          </div>
        </div>
        <div className="niuu-live-telemetry__tools-empty">No tool spans recorded yet.</div>
      </section>
    );
  }

  return (
    <section className="niuu-live-telemetry__tools-shell" data-testid="telemetry-tools-overview">
      <div className="niuu-live-telemetry__tools-head">
        <div className="niuu-live-telemetry__tools-title-row">
          <span className="niuu-live-telemetry__tools-title">Tool calls</span>
          <span className="niuu-live-telemetry__tools-summary">
            {overview.totalToolCalls} calls
            <span aria-hidden="true">&middot;</span>
            {formatDurationMs(overview.totalToolMs)} total
            <span aria-hidden="true">&middot;</span>
            {`${trace.durationMs > 0 ? Math.round((overview.totalToolMs / trace.durationMs) * 100) : 0}% of session`}
          </span>
        </div>

        <div className="niuu-live-telemetry__tools-filter-row">
          {overview.primaryFilters.map((filter) => (
            <button
              key={filter.key}
              type="button"
              className={cn(
                'niuu-live-telemetry__tools-filter',
                selectedCategory === filter.key && 'niuu-live-telemetry__tools-filter--selected',
              )}
              onClick={() => setSelectedCategory(filter.key as TelemetryToolCategory)}
            >
              <span
                className={cn(
                  'niuu-live-telemetry__tools-filter-dot',
                  `niuu-live-telemetry__tools-filter-dot--${filter.tone}`,
                )}
              />
              {filter.label}
              <span className="niuu-live-telemetry__tools-filter-count">{filter.count}</span>
            </button>
          ))}
        </div>

        {secondaryFilters.length > 0 ? (
          <div className="niuu-live-telemetry__tools-filter-row niuu-live-telemetry__tools-filter-row--secondary">
            {secondaryFilters.map((filter) => (
              <button
                key={filter.key}
                type="button"
                className={cn(
                  'niuu-live-telemetry__tools-filter niuu-live-telemetry__tools-filter--secondary',
                  selectedGroupId === filter.key && 'niuu-live-telemetry__tools-filter--selected',
                )}
                onClick={() => setSelectedGroupId(String(filter.key))}
              >
                <span
                  className={cn(
                    'niuu-live-telemetry__tools-filter-dot',
                    `niuu-live-telemetry__tools-filter-dot--${filter.tone}`,
                  )}
                />
                {filter.label}
                <span className="niuu-live-telemetry__tools-filter-count">{filter.count}</span>
              </button>
            ))}
          </div>
        ) : null}
      </div>

      <div className="niuu-live-telemetry__tools-table">
        <div className="niuu-live-telemetry__tools-columns">
          <span>Tool</span>
          <span>Calls</span>
          <span>Total</span>
          <span>P50</span>
          <span>P95</span>
          <span>Max</span>
          <span>Share</span>
        </div>

        <div className="niuu-live-telemetry__tools-rows">
          {visibleRows.map((row) => (
            <div key={row.id} className="niuu-live-telemetry__tools-row">
              <div className="niuu-live-telemetry__tools-row-main">
                <div className="niuu-live-telemetry__tools-tool-cell">
                  <span className="niuu-live-telemetry__tools-badge">{row.badge}</span>
                  <div className="niuu-live-telemetry__tools-tool-copy">
                    <span className="niuu-live-telemetry__tools-tool-title">{row.title}</span>
                    {row.subtitle ? (
                      <span className="niuu-live-telemetry__tools-tool-subtitle">
                        {row.subtitle}
                      </span>
                    ) : null}
                  </div>
                </div>
                <div className="niuu-live-telemetry__tools-metric">{row.calls}</div>
                <div className="niuu-live-telemetry__tools-metric">
                  {formatDurationMs(row.totalMs)}
                </div>
                <div className="niuu-live-telemetry__tools-metric niuu-live-telemetry__tools-metric--muted">
                  {formatCompactDurationMs(row.p50Ms)}
                </div>
                <div className="niuu-live-telemetry__tools-metric">
                  {formatDurationMs(row.p95Ms)}
                </div>
                <div className="niuu-live-telemetry__tools-metric">
                  {formatDurationMs(row.maxMs)}
                </div>
                <div className="niuu-live-telemetry__tools-share-cell">
                  <div className="niuu-live-telemetry__tools-share-track">
                    <span
                      className="niuu-live-telemetry__tools-share-fill"
                      style={{ width: `${Math.max(row.sharePercent, row.totalMs > 0 ? 1 : 0)}%` }}
                    />
                  </div>
                  <span className="niuu-live-telemetry__tools-share-label">
                    {row.sharePercent}%
                  </span>
                </div>
              </div>
              {row.blockedCount > 0 ? (
                <div className="niuu-live-telemetry__tools-row-note">
                  ↳ blocked {row.blockedCount}× • {formatCompactDurationMs(row.blockedMs)}
                  {row.category === 'mcp' ? ` on ${row.title}` : ''}
                </div>
              ) : null}
            </div>
          ))}
        </div>

        <div className="niuu-live-telemetry__tools-footnote">
          p50 = median latency · p95 = slowest 5% · max = slowest single call
        </div>
      </div>
    </section>
  );
}

function TelemetryMetricCard({
  label,
  value,
  detail,
  accent = false,
  emphasize = false,
}: {
  label: string;
  value: string;
  detail: string;
  accent?: boolean;
  emphasize?: boolean;
}) {
  return (
    <article
      className={cn(
        'niuu-live-telemetry__metric-card',
        accent && 'niuu-live-telemetry__metric-card--accent',
      )}
    >
      <div className="niuu-live-telemetry__metric-label">{label}</div>
      <div
        className={cn(
          'niuu-live-telemetry__metric-value',
          emphasize && 'niuu-live-telemetry__metric-value--emphasized',
        )}
      >
        {value}
      </div>
      <div className="niuu-live-telemetry__metric-detail">{detail}</div>
    </article>
  );
}

function TelemetryTab({
  sessionId,
  session,
  runLabel,
  volundr,
  isRunning,
}: {
  sessionId: string;
  session: VolundrSession | null | undefined;
  runLabel: string;
  volundr: IVolundrService;
  isRunning: boolean;
}) {
  const traceQuery = useQuery({
    queryKey: ['volundr', 'session-trace', sessionId],
    queryFn: () => volundr.getSessionTrace(sessionId),
    refetchInterval: isRunning ? 5_000 : false,
    staleTime: 5_000,
  });
  const summaryQuery = useQuery({
    queryKey: ['volundr', 'session-trace-summary', sessionId],
    queryFn: () => volundr.getSessionTraceSummary(sessionId),
    refetchInterval: isRunning ? 5_000 : false,
    staleTime: 5_000,
  });
  const baselineQuery = useQuery({
    queryKey: ['volundr', 'session-trace-baseline', sessionId, deriveComparableSourceKey(session)],
    queryFn: async () => {
      const sessions = await volundr.getSessions();
      const sourceKey = deriveComparableSourceKey(session);
      const sourceMatchedSessions = sessions
        .filter(
          (candidate) =>
            candidate.id !== sessionId &&
            candidate.status !== 'running' &&
            deriveComparableSourceKey(candidate) === sourceKey,
        )
        .sort((left, right) => right.lastActive - left.lastActive)
        .slice(0, 8);
      const fallbackSessions = sessions
        .filter((candidate) => candidate.id !== sessionId && candidate.status !== 'running')
        .sort((left, right) => right.lastActive - left.lastActive)
        .slice(0, 8);
      async function loadDurations(candidates: VolundrSession[]): Promise<number[]> {
        const summaries = await Promise.allSettled(
          candidates.map((candidate) => volundr.getSessionTraceSummary(candidate.id)),
        );
        return summaries
          .flatMap((result) =>
            result.status === 'fulfilled' && result.value?.totalDurationMs
              ? [result.value.totalDurationMs]
              : [],
          )
          .filter((value) => value > 0);
      }
      let durations = await loadDurations(sourceMatchedSessions);
      if (durations.length === 0) {
        durations = await loadDurations(fallbackSessions);
      }
      return {
        sampleCount: durations.length,
        medianMs: median(durations),
      };
    },
    enabled: Boolean(session),
    staleTime: 10_000,
  });

  const trace = traceQuery.data;
  const summary = summaryQuery.data;
  const timelineRows = useMemo(() => (trace ? buildTelemetryTimelineRows(trace) : []), [trace]);
  const [selectedStageIdState, setSelectedStageId] = useState<string | null>(null);
  const [expandedStageIdsState, setExpandedStageIds] = useState<Set<string>>(new Set());

  const metricModel = useMemo(() => {
    if (!trace || !summary) return null;
    const totalDurationMs = summary.totalDurationMs || trace.durationMs || 0;
    const activeDurationMs = summary.activeExecutionDurationMs || 0;
    const publishDurationMs = summary.publishDurationMs || 0;
    const queueWaitDurationMs = Math.max(
      summary.waitingDurationMs || 0,
      totalDurationMs -
        activeDurationMs -
        (summary.provisioningDurationMs || 0) -
        (summary.setupDurationMs || 0) -
        publishDurationMs -
        (summary.cleanupDurationMs || 0),
      0,
    );
    const longestStage = pickLongestStage(trace, summary);
    const trackedSpanCount = trace.spans.filter((span) => span.parentSpanId).length;
    const baselineMedianMs = baselineQuery.data?.medianMs ?? null;
    const baselineDeltaMs = baselineMedianMs != null ? totalDurationMs - baselineMedianMs : null;

    return {
      totalDurationMs,
      activeDurationMs,
      queueWaitDurationMs,
      publishDurationMs,
      longestStage,
      trackedSpanCount,
      baselineMedianMs,
      baselineDeltaMs,
      baselineSampleCount: baselineQuery.data?.sampleCount ?? 0,
    };
  }, [baselineQuery.data?.medianMs, baselineQuery.data?.sampleCount, summary, trace]);

  const fallbackStageId =
    timelineRows.find((row) => row.id === metricModel?.longestStage?.id)?.id ??
    timelineRows[0]?.id ??
    null;
  const selectedStageId =
    timelineRows.find((row) => row.id === selectedStageIdState)?.id ?? fallbackStageId;
  const expandedStageIds = useMemo(() => {
    const validStageIds = new Set(timelineRows.map((row) => row.id));
    if (validStageIds.size === 0) return new Set<string>();
    const next = new Set([...expandedStageIdsState].filter((id) => validStageIds.has(id)));
    if (fallbackStageId && next.size === 0) {
      next.add(fallbackStageId);
    }
    return next;
  }, [expandedStageIdsState, fallbackStageId, timelineRows]);

  const handleSelectStage = useCallback((rowId: string) => {
    setSelectedStageId(rowId);
    setExpandedStageIds((current) => {
      const next = new Set(current);
      next.add(rowId);
      return next;
    });
  }, []);

  const handleToggleStage = useCallback((rowId: string) => {
    setExpandedStageIds((current) => {
      const next = new Set(current);
      if (next.has(rowId)) {
        next.delete(rowId);
      } else {
        next.add(rowId);
      }
      return next;
    });
  }, []);

  if (traceQuery.isLoading || summaryQuery.isLoading) {
    return (
      <div className="niuu-live-telemetry" data-testid="live-telemetry-tab">
        <div className="niuu-live-telemetry__shell niuu-live-telemetry__shell--loading">
          <div className="niuu-live-telemetry__loading">Loading trace telemetry…</div>
        </div>
      </div>
    );
  }

  if (traceQuery.isError || summaryQuery.isError) {
    return (
      <div className="niuu-live-telemetry" data-testid="live-telemetry-tab">
        <div className="niuu-live-telemetry__shell niuu-live-telemetry__shell--empty">
          <div className="niuu-live-telemetry__empty-title">Telemetry unavailable</div>
          <p className="niuu-live-telemetry__empty-copy">
            Trace data could not be loaded for this session yet.
          </p>
        </div>
      </div>
    );
  }

  if (!trace || !summary || !metricModel) {
    return (
      <div className="niuu-live-telemetry" data-testid="live-telemetry-tab">
        <div className="niuu-live-telemetry__shell niuu-live-telemetry__shell--empty">
          <div className="niuu-live-telemetry__empty-title">No trace recorded yet</div>
          <p className="niuu-live-telemetry__empty-copy">
            Start a traced session or workflow and the first telemetry strip will appear here.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="niuu-live-telemetry" data-testid="live-telemetry-tab">
      <div className="niuu-live-telemetry__header-row">
        <div className="niuu-live-telemetry__run-group">
          <span className="niuu-live-telemetry__section-label">Run</span>
          <div className="niuu-live-telemetry__run-pill">
            <span className="niuu-live-telemetry__run-pill-label">{runLabel}</span>
            <span className="niuu-live-telemetry__run-pill-divider" aria-hidden="true">
              •
            </span>
            <span>{formatTraceStamp(trace.startedAt)}</span>
            <span aria-hidden="true">→</span>
            <span>{formatTraceStamp(trace.endedAt)}</span>
          </div>
        </div>
        <div className="niuu-live-telemetry__legend" aria-label="Telemetry legend">
          <span className="niuu-live-telemetry__legend-item">
            <span className="niuu-live-telemetry__swatch niuu-live-telemetry__swatch--active" />
            active
          </span>
          <span className="niuu-live-telemetry__legend-item">
            <span className="niuu-live-telemetry__swatch niuu-live-telemetry__swatch--wait" />
            wait
          </span>
          <span className="niuu-live-telemetry__legend-item">
            <span className="niuu-live-telemetry__swatch niuu-live-telemetry__swatch--blocked" />
            blocked
          </span>
        </div>
      </div>

      <div className="niuu-live-telemetry__shell">
        <div className="niuu-live-telemetry__metrics">
          <TelemetryMetricCard
            label="Session Duration"
            value={formatDurationMs(metricModel.totalDurationMs)}
            detail={`${metricModel.trackedSpanCount} tracked spans`}
            accent
          />
          <TelemetryMetricCard
            label="Active Work"
            value={formatDurationMs(metricModel.activeDurationMs)}
            detail={formatPercentOfTotal(metricModel.activeDurationMs, metricModel.totalDurationMs)}
          />
          <TelemetryMetricCard
            label="Queue • Wait"
            value={formatDurationMs(metricModel.queueWaitDurationMs)}
            detail={formatPercentOfTotal(
              metricModel.queueWaitDurationMs,
              metricModel.totalDurationMs,
            )}
            accent
          />
          <TelemetryMetricCard
            label="Publish"
            value={formatDurationMs(metricModel.publishDurationMs)}
            detail={formatPercentOfTotal(
              metricModel.publishDurationMs,
              metricModel.totalDurationMs,
            )}
          />
          <TelemetryMetricCard
            label="Longest Stage"
            value={formatStageLabel(metricModel.longestStage)}
            detail={formatDurationMs(metricModel.longestStage?.durationMs ?? 0)}
            emphasize
          />
          <article className="niuu-live-telemetry__metric-card">
            <div className="niuu-live-telemetry__metric-label">Vs Recent Median</div>
            {metricModel.baselineMedianMs != null ? (
              <>
                <div
                  className={cn(
                    'niuu-live-telemetry__delta-pill',
                    (metricModel.baselineDeltaMs ?? 0) > 0
                      ? 'niuu-live-telemetry__delta-pill--slower'
                      : 'niuu-live-telemetry__delta-pill--faster',
                  )}
                >
                  {formatSignedDuration(metricModel.baselineDeltaMs ?? 0)}
                </div>
                <div className="niuu-live-telemetry__metric-detail">
                  {`median ${formatDurationMs(metricModel.baselineMedianMs)} • n=${metricModel.baselineSampleCount}`}
                </div>
              </>
            ) : (
              <>
                <div className="niuu-live-telemetry__metric-value">no baseline</div>
                <div className="niuu-live-telemetry__metric-detail">
                  Need at least one comparable traced session
                </div>
              </>
            )}
          </article>
        </div>
      </div>

      <TelemetryTimeline
        trace={trace}
        selectedRowId={selectedStageId}
        onSelectRow={handleSelectStage}
      />

      <div className="niuu-live-telemetry__detail-grid">
        <div className="niuu-live-telemetry__detail-grid-main">
          <TelemetryStageBreakdown
            trace={trace}
            rows={timelineRows}
            selectedRowId={selectedStageId}
            expandedRowIds={expandedStageIds}
            onSelectRow={handleSelectStage}
            onToggleRow={handleToggleStage}
          />
        </div>
        <div className="niuu-live-telemetry__detail-grid-side">
          <TelemetryToolCallsOverview trace={trace} />
        </div>
      </div>

      <TelemetryTurnByTurn trace={trace} />
    </div>
  );
}

export function eventTone(type: SessionChronicle['events'][number]['type']) {
  switch (type) {
    case 'message':
      return {
        badge: 'niuu-text-sky-300 niuu-bg-sky-500/12 niuu-border-sky-500/30',
        dot: 'niuu-bg-sky-400',
      };
    case 'file':
      return {
        badge: 'niuu-text-emerald-300 niuu-bg-emerald-500/12 niuu-border-emerald-500/30',
        dot: 'niuu-bg-emerald-400',
      };
    case 'git':
      return {
        badge: 'niuu-text-violet-300 niuu-bg-violet-500/12 niuu-border-violet-500/30',
        dot: 'niuu-bg-violet-400',
      };
    case 'terminal':
      return {
        badge: 'niuu-text-amber-300 niuu-bg-amber-500/12 niuu-border-amber-500/30',
        dot: 'niuu-bg-amber-400',
      };
    case 'error':
      return {
        badge: 'niuu-text-rose-300 niuu-bg-rose-500/12 niuu-border-rose-500/30',
        dot: 'niuu-bg-rose-400',
      };
    default:
      return {
        badge: 'niuu-text-text-secondary niuu-bg-bg-elevated niuu-border-border-subtle',
        dot: 'niuu-bg-text-muted',
      };
  }
}

export function eventIcon(type: SessionChronicle['events'][number]['type']) {
  switch (type) {
    case 'message':
      return MessageSquareText;
    case 'file':
      return FilePenLine;
    case 'git':
      return GitCommitHorizontal;
    case 'terminal':
      return SquareTerminal;
    case 'error':
      return AlertTriangle;
    default:
      return ScrollText;
  }
}

export function eventLabel(type: SessionChronicle['events'][number]['type']): string {
  switch (type) {
    case 'message':
      return 'Message';
    case 'file':
      return 'File';
    case 'git':
      return 'Commit';
    case 'terminal':
      return 'Terminal';
    case 'error':
      return 'Error';
    default:
      return 'Session';
  }
}

export function truncateLeadingPath(value: string, maxLength = 42): string {
  if (value.length <= maxLength) return value;
  const parts = value.split('/').filter(Boolean);
  if (parts.length <= 2) return `…${value.slice(-(maxLength - 1))}`;
  let suffix = parts[parts.length - 1] ?? value;
  let index = parts.length - 2;
  while (index >= 0) {
    const candidate = `${parts[index]}/${suffix}`;
    if (candidate.length + 2 > maxLength) break;
    suffix = candidate;
    index -= 1;
  }
  return `…/${suffix}`;
}

export async function copyText(text: string): Promise<boolean> {
  if (typeof navigator === 'undefined' || !navigator.clipboard?.writeText) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export function normalizeRepoLink(source: VolundrSession['source'] | null | undefined) {
  if (!source || source.type !== 'git' || !source.repo) return null;
  if (source.repo.startsWith('http://') || source.repo.startsWith('https://')) return source.repo;
  return `https://github.com/${source.repo}`;
}

export function formatRepoLabel(repo: string): string {
  const trimmed = repo.replace(/\.git$/, '');
  const sshMatch = trimmed.match(/^[^@]+@([^:]+):(.+)$/);
  if (sshMatch) return sshMatch[2] ?? trimmed;
  if (/^[^/]+\/[^/]+$/.test(trimmed)) return trimmed;
  const candidate =
    trimmed.startsWith('http://') || trimmed.startsWith('https://')
      ? trimmed
      : `https://${trimmed}`;
  try {
    const url = new URL(candidate);
    const path = url.pathname.replace(/^\/+/, '');
    if (!path) return trimmed;
    if (['github.com', 'gitlab.com', 'bitbucket.org'].includes(url.hostname)) return path;
    return `${url.hostname}/${path}`;
  } catch {
    return trimmed;
  }
}

export function truncateMiddle(value: string, maxLength = 30): string {
  if (value.length <= maxLength) return value;
  const start = Math.ceil((maxLength - 3) * 0.6);
  const end = Math.floor((maxLength - 3) * 0.4);
  return `${value.slice(0, start)}...${value.slice(-end)}`;
}

function titleCaseWord(value: string): string {
  if (!value) return value;
  return `${value.slice(0, 1).toUpperCase()}${value.slice(1)}`;
}

function normalizeForgeBadgeLabel(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return trimmed;
  if (/^local-[a-f0-9]{6,}$/i.test(trimmed)) return 'Local';
  return titleCaseWord(trimmed);
}

function SourceMeta({ session }: { session: VolundrSession | null | undefined }) {
  const [branchCopied, setBranchCopied] = useState(false);

  if (!session?.source) return null;

  if (session.source.type === 'git') {
    const repoUrl = normalizeRepoLink(session.source);
    const repoLabel = formatRepoLabel(session.source.repo);
    const branch = session.source.branch ?? 'main';

    return (
      <span className="niuu-live-session__source">
        <span className="niuu-text-text-faint" aria-hidden>
          ›
        </span>
        {repoUrl ? (
          <a
            href={repoUrl}
            target="_blank"
            rel="noreferrer"
            className="niuu-live-session__source-link"
            title={repoUrl}
          >
            {repoLabel}
          </a>
        ) : (
          <span className="niuu-live-session__source-link" title={session.source.repo}>
            {repoLabel}
          </span>
        )}
        <button
          type="button"
          className="niuu-live-session__branch-button"
          title={`${branchCopied ? 'Copied' : branch} · click to copy`}
          onClick={async () => {
            const copied = await copyText(branch);
            setBranchCopied(copied);
            if (copied) {
              setTimeout(() => setBranchCopied(false), 1200);
            }
          }}
        >
          {`@${truncateMiddle(branch, 18)}`}
        </button>
      </span>
    );
  }

  const path = session.source.path ?? 'local mount';
  return (
    <span className="niuu-live-session__source">
      <span className="niuu-text-text-faint" aria-hidden>
        ›
      </span>
      <span className="niuu-live-session__source-link" title={path}>
        {path}
      </span>
    </span>
  );
}

function HeaderMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="niuu-live-session__metric">
      <span className="niuu-live-session__metric-label">{label}</span>
      <span className="niuu-live-session__metric-value">{value}</span>
    </div>
  );
}

function HeaderDivider() {
  return <span className="niuu-live-session__divider" aria-hidden="true" />;
}

function HeaderActionButton({
  label,
  onClick,
  disabled,
  tone = 'neutral',
  className,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: 'neutral' | 'critical' | 'brand';
  className?: string;
}) {
  const toneClass =
    tone === 'critical'
      ? 'niuu-border-rose-500/35 niuu-bg-rose-500/10 niuu-text-rose-200 hover:niuu-bg-rose-500/15'
      : tone === 'brand'
        ? 'niuu-border-brand/40 niuu-bg-brand/12 niuu-text-brand hover:niuu-bg-brand/18'
        : 'niuu-border-border-subtle niuu-bg-bg-elevated niuu-text-text-secondary hover:niuu-bg-bg-tertiary';

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'niuu-inline-flex niuu-appearance-none niuu-items-center niuu-justify-center niuu-rounded-md niuu-border niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-[11px] niuu-transition-colors disabled:niuu-cursor-not-allowed disabled:niuu-opacity-45',
        toneClass,
        className,
      )}
    >
      {label}
    </button>
  );
}

function TabCountBadge({ count }: { count?: number }) {
  if (count == null || count <= 0) return null;
  return <span className="niuu-live-session__tab-count">{formatCount(count)}</span>;
}

function SessionToolbarButton({
  icon: Icon,
  title,
  onClick,
  disabled,
  tone = 'neutral',
  active = false,
  label,
}: {
  icon: typeof Eye;
  title: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: 'neutral' | 'critical' | 'brand';
  active?: boolean;
  label?: string;
}) {
  const toneClass =
    tone === 'critical'
      ? 'niuu-live-session__toolbar-button--critical'
      : tone === 'brand'
        ? 'niuu-live-session__toolbar-button--brand'
        : 'niuu-live-session__toolbar-button--neutral';

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={title}
      title={title}
      aria-pressed={active || undefined}
      className={cn(
        'niuu-live-session__toolbar-button',
        active && 'niuu-live-session__toolbar-button--active',
        toneClass,
      )}
    >
      <Icon className="niuu-live-session__toolbar-icon" />
      {label ? <span className="niuu-live-session__toolbar-label">{label}</span> : null}
    </button>
  );
}

function SessionIdChip({ sessionId }: { sessionId: string }) {
  return (
    <button
      type="button"
      title={`${sessionId} · click to copy`}
      aria-label="Copy session ID"
      data-testid="session-id-label"
      onClick={async () => {
        await copyText(sessionId);
      }}
      className="niuu-live-session__session-id"
    >
      {sessionId}
    </button>
  );
}

function TicketLink({ issue }: { issue: VolundrSession['trackerIssue'] }) {
  if (!issue) return null;
  if (!issue.url) {
    return (
      <span
        className="niuu-live-session__ticket niuu-live-session__ticket--static"
        title={issue.identifier}
      >
        <span>{issue.identifier}</span>
      </span>
    );
  }
  return (
    <a
      href={issue.url}
      target="_blank"
      rel="noreferrer"
      className="niuu-live-session__ticket"
      title={issue.identifier}
    >
      <span>{issue.identifier}</span>
      <ExternalLink className="niuu-h-3.5 niuu-w-3.5" />
    </a>
  );
}

function SessionForgeBadge({ label, tone }: { label: string; tone?: string }) {
  return (
    <span className="niuu-live-session__forge-badge">
      <span className="niuu-live-session__forge-badge-label">{label}</span>
      <span className="niuu-live-session__forge-badge-tone">{tone ?? 'PRIMARY'}</span>
    </span>
  );
}

function DeleteSessionDialog({
  open,
  session,
  onClose,
  onConfirm,
  busy,
}: {
  open: boolean;
  session: VolundrSession | null;
  onClose: () => void;
  onConfirm: (cleanup: string[]) => void;
  busy: boolean;
}) {
  const cleanupKey = open && session ? session.id : 'closed';
  const [cleanupState, setCleanupState] = useState<{ key: string; value: Set<string> } | null>(
    null,
  );
  const cleanup = cleanupState?.key === cleanupKey ? cleanupState.value : new Set<string>();

  if (!open || !session) return null;

  const isManual = session.origin === 'manual';
  const isLocalStorage = session.source.type === 'local_mount';

  function toggleCleanup(key: string) {
    setCleanupState((prev) => {
      const next = new Set(prev?.key === cleanupKey ? prev.value : cleanup);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return { key: cleanupKey, value: next };
    });
  }

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <DialogContent
        title={isManual ? 'Remove session' : 'Delete session'}
        description={
          isManual
            ? `Remove ${session.name} from the session list?`
            : `Delete ${session.name}? This action cannot be undone.`
        }
        className="niuu-live-delete-dialog"
      >
        {!isManual && (
          <div className="niuu-live-delete-dialog__cleanup">
            <div className="niuu-live-delete-dialog__cleanup-label">Also clean up</div>
            <div className="niuu-live-delete-dialog__cleanup-list">
              <label
                className={cn(
                  'niuu-live-delete-dialog__cleanup-option',
                  isLocalStorage && 'niuu-live-delete-dialog__cleanup-option--disabled',
                )}
              >
                <input
                  type="checkbox"
                  checked={cleanup.has('workspace_storage')}
                  onChange={() => toggleCleanup('workspace_storage')}
                  disabled={isLocalStorage}
                  data-testid="cleanup-workspace_storage"
                  className="niuu-sr-only"
                />
                <span
                  aria-hidden="true"
                  className={cn(
                    'niuu-live-delete-dialog__checkbox',
                    cleanup.has('workspace_storage') &&
                      'niuu-live-delete-dialog__checkbox--checked',
                    isLocalStorage && 'niuu-live-delete-dialog__checkbox--disabled',
                  )}
                >
                  {cleanup.has('workspace_storage') ? (
                    <Check className="niuu-h-3 niuu-w-3" />
                  ) : null}
                </span>
                <div>
                  <div className="niuu-text-sm niuu-text-text-primary">
                    Delete workspace storage
                  </div>
                  <div className="niuu-mt-1 niuu-text-xs niuu-text-text-muted">
                    {isLocalStorage
                      ? 'Local mounted workspace — manage storage on your machine.'
                      : 'Permanently delete the workspace storage so future sessions cannot reuse it.'}
                  </div>
                </div>
              </label>
              <label className="niuu-live-delete-dialog__cleanup-option">
                <input
                  type="checkbox"
                  checked={cleanup.has('chronicles')}
                  onChange={() => toggleCleanup('chronicles')}
                  data-testid="cleanup-chronicles"
                  className="niuu-sr-only"
                />
                <span
                  aria-hidden="true"
                  className={cn(
                    'niuu-live-delete-dialog__checkbox',
                    cleanup.has('chronicles') && 'niuu-live-delete-dialog__checkbox--checked',
                  )}
                >
                  {cleanup.has('chronicles') ? <Check className="niuu-h-3 niuu-w-3" /> : null}
                </span>
                <div>
                  <div className="niuu-text-sm niuu-text-text-primary">Delete chronicles</div>
                  <div className="niuu-mt-1 niuu-text-xs niuu-text-text-muted">
                    Remove timeline history and chronicle records for this session.
                  </div>
                </div>
              </label>
            </div>
          </div>
        )}

        <div className="niuu-live-delete-dialog__actions">
          <HeaderActionButton
            label="cancel"
            onClick={onClose}
            disabled={busy}
            className="niuu-live-delete-dialog__action-button"
          />
          <HeaderActionButton
            label={busy ? (isManual ? 'removing…' : 'deleting…') : isManual ? 'remove' : 'delete'}
            onClick={() => onConfirm(Array.from(cleanup))}
            disabled={busy}
            tone="critical"
            className="niuu-live-delete-dialog__action-button niuu-live-delete-dialog__action-button--critical"
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}

function LiveLogsTab({ sessionId, volundr }: { sessionId: string; volundr: IVolundrService }) {
  const [level, setLevel] = useState('DEBUG');
  const requestKey = `${sessionId}:${level}`;
  const [logState, setLogState] = useState<{
    key: string;
    loading: boolean;
    logs: {
      lines: VolundrAggregatedLog[];
      participants: VolundrLogParticipant[];
    };
  }>({
    key: requestKey,
    loading: true,
    logs: { lines: [], participants: [] },
  });
  const logs =
    logState.key === requestKey ? logState.logs : { lines: [], participants: [] };
  const loading = logState.key === requestKey ? logState.loading : true;

  useEffect(() => {
    let active = true;

    void volundr
      .getAggregatedLogs(sessionId, { limit: 400, level })
      .then((payload) => {
        if (!active) return;
        setLogState({ key: requestKey, logs: payload, loading: false });
      })
      .catch(() => {
        if (!active) return;
        setLogState({
          key: requestKey,
          logs: { lines: [], participants: [] },
          loading: false,
        });
      });

    const unsubscribe = volundr.subscribeAggregatedLogs(
      sessionId,
      { limit: 400, level },
      (payload) => {
        if (!active) return;
        setLogState({ key: requestKey, logs: payload, loading: false });
      },
    );

    return () => {
      active = false;
      unsubscribe();
    };
  }, [level, requestKey, sessionId, volundr]);

  const participants = useMemo<VolundrLogParticipant[]>(() => {
    if (logs.participants.length > 0) return logs.participants;
    const seen = new Map<string, VolundrLogParticipant>();
    for (const line of logs.lines) {
      if (seen.has(line.participant)) continue;
      seen.set(line.participant, {
        id: line.participant,
        label: line.participantLabel,
        kind: line.participantKind,
      });
    }
    return Array.from(seen.values());
  }, [logs.lines, logs.participants]);

  return (
    <div className="niuu-flex niuu-h-full niuu-flex-col" data-testid="live-logs-tab">
      <StructuredLogViewer
        logs={logs.lines}
        participants={participants}
        loading={loading}
        initialLevel={level as 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'}
        level={level as 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'}
        onLevelChange={setLevel}
        downloadFilename={`${sessionId}-logs.log`}
        toolbarTestId="live-logs-toolbar"
      />
    </div>
  );
}

function LiveChroniclesTab({
  sessionId,
  sessionStatus,
  session,
  volundr,
}: {
  sessionId: string;
  sessionStatus: string | null;
  session: VolundrSession | null;
  volundr: IVolundrService;
}) {
  const [chronicleState, setChronicleState] = useState<{
    key: string;
    loading: boolean;
    chronicle: SessionChronicle | null;
  }>({ key: sessionId, loading: true, chronicle: null });
  const chronicle = chronicleState.key === sessionId ? chronicleState.chronicle : null;
  const loading = chronicleState.key === sessionId ? chronicleState.loading : true;
  const [copiedPath, setCopiedPath] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void volundr.getChronicle(sessionId).then((payload) => {
      if (!active) return;
      setChronicleState({ key: sessionId, chronicle: payload, loading: false });
    });
    const unsubscribe = volundr.subscribeChronicle(sessionId, (payload) => {
      if (!active) return;
      setChronicleState({ key: sessionId, chronicle: payload, loading: false });
    });
    return () => {
      active = false;
      unsubscribe();
    };
  }, [sessionId, volundr]);

  const handleCopyPath = useCallback((path: string) => {
    void copyText(path).then((ok) => {
      if (!ok) return;
      setCopiedPath(path);
      window.setTimeout(() => {
        setCopiedPath((current) => (current === path ? null : current));
      }, 1400);
    });
  }, []);

  if (loading) {
    return (
      <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-text-muted">
        Loading chronicle…
      </div>
    );
  }

  if (!chronicle) {
    return (
      <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-text-muted">
        {sessionStatus === 'running' ? 'No chronicle data yet.' : 'No saved chronicle yet.'}
      </div>
    );
  }

  const maxBurn = Math.max(1, ...chronicle.tokenBurn);
  const totalTokens = chronicle.events.reduce((sum, event) => sum + (event.tokens ?? 0), 0);

  return (
    <div className="niuu-live-chronicles-layout">
      <div className="niuu-flex niuu-min-h-0 niuu-flex-col niuu-border-r niuu-border-border-subtle niuu-bg-bg-primary">
        <div className="niuu-border-b niuu-border-border-subtle niuu-px-4 niuu-py-2.5">
          <div className="niuu-flex niuu-items-center niuu-justify-between niuu-gap-4">
            <div className="niuu-min-w-0">
              <div className="niuu-whitespace-nowrap niuu-text-sm niuu-font-medium niuu-text-text-primary">
                Event timeline
              </div>
              <div className="niuu-mt-1 niuu-whitespace-nowrap niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
                {chronicle.events.length} events · {formatCount(totalTokens)} tokens
              </div>
            </div>
            <div className="niuu-flex niuu-h-12 niuu-items-end niuu-gap-1">
              {chronicle.tokenBurn.map((value, index) => (
                <span
                  key={`${value}-${index}`}
                  className={cn(
                    'niuu-w-2 niuu-rounded-sm niuu-bg-brand/30',
                    value >= maxBurn * 0.75 && 'niuu-bg-brand/60',
                  )}
                  style={{ height: `${Math.max(14, (value / maxBurn) * 48)}px` }}
                  title={`${value} tokens`}
                />
              ))}
            </div>
          </div>
        </div>

        <div className="niuu-min-h-0 niuu-flex-1 niuu-overflow-auto niuu-px-4 niuu-py-3">
          <div className="niuu-live-chronicles-timeline">
            <div className="niuu-live-chronicles-rail" />
            <div className="niuu-flex niuu-flex-col">
              {chronicle.events.map((event, index) => {
                const tone = eventTone(event.type);
                const Icon = eventIcon(event.type);
                return (
                  <div
                    key={`${event.type}-${event.t}-${index}`}
                    className="niuu-live-chronicles-row niuu-border-b niuu-border-border-subtle/60 niuu-py-2.5 last:niuu-border-b-0"
                  >
                    <div className="niuu-pt-1 niuu-text-right niuu-font-mono niuu-text-[10px] niuu-text-text-faint">
                      {formatEventTime(event.t)}
                    </div>
                    <div className="niuu-relative niuu-flex niuu-justify-center">
                      <span
                        className={cn(
                          'niuu-relative niuu-z-[1] niuu-mt-1.5 niuu-inline-flex niuu-h-5 niuu-w-5 niuu-items-center niuu-justify-center niuu-rounded-md niuu-border niuu-bg-bg-secondary',
                          tone.badge,
                        )}
                      >
                        <Icon className="niuu-h-3 niuu-w-3" />
                      </span>
                    </div>
                    <div className="niuu-min-w-0">
                      <div className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-3">
                        <div className="niuu-flex niuu-min-w-0 niuu-items-center niuu-gap-2">
                          <span className="niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.16em] niuu-text-text-faint">
                            {eventLabel(event.type)}
                          </span>
                          {event.action ? (
                            <span className="niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-muted">
                              {event.action}
                            </span>
                          ) : null}
                        </div>
                        <div className="niuu-flex niuu-flex-wrap niuu-justify-end niuu-gap-x-2 niuu-gap-y-1 niuu-font-mono niuu-text-[10px]">
                          {typeof event.tokens === 'number' && (
                            <span className="niuu-text-text-muted">
                              {formatCount(event.tokens)} tok
                            </span>
                          )}
                          {(typeof event.ins === 'number' || typeof event.del === 'number') && (
                            <span className="niuu-text-text-muted">
                              {typeof event.ins === 'number' ? `+${event.ins}` : ''}
                              {typeof event.del === 'number' ? ` / -${event.del}` : ''}
                            </span>
                          )}
                          {event.hash && <span className="niuu-text-text-faint">{event.hash}</span>}
                          {typeof event.exit === 'number' && (
                            <span
                              className={
                                event.exit === 0 ? 'niuu-text-emerald-300' : 'niuu-text-rose-300'
                              }
                            >
                              exit {event.exit}
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="niuu-mt-1 niuu-text-[13px] niuu-leading-5 niuu-text-text-primary">
                        {event.label}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      <div className="niuu-flex niuu-min-h-0 niuu-flex-col niuu-overflow-auto niuu-bg-bg-secondary niuu-p-3">
        {session?.trackerIssue && (
          <section className="niuu-mb-3 niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-p-3">
            <div className="niuu-mb-2 niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-faint">
              Tracker
            </div>
            <a
              href={session.trackerIssue.url}
              target="_blank"
              rel="noreferrer"
              className="niuu-inline-flex niuu-items-center niuu-gap-2 niuu-text-sm niuu-text-brand hover:niuu-underline"
            >
              <span className="niuu-rounded-md niuu-border niuu-border-brand/35 niuu-bg-brand/10 niuu-px-2 niuu-py-0.5 niuu-font-mono niuu-text-[11px]">
                {session.trackerIssue.identifier}
              </span>
              <span className="niuu-text-text-primary">{session.trackerIssue.title}</span>
            </a>
          </section>
        )}

        <section className="niuu-mb-3 niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-p-3">
          <div className="niuu-mb-2.5 niuu-flex niuu-items-center niuu-justify-between">
            <div className="niuu-text-sm niuu-font-medium niuu-text-text-primary">
              Files modified
            </div>
            <div className="niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
              {chronicle.files.length}
            </div>
          </div>
          <div className="niuu-flex niuu-flex-col">
            {chronicle.files.length > 0 ? (
              chronicle.files.map((file, index) => (
                <button
                  key={file.path}
                  type="button"
                  onClick={() => handleCopyPath(file.path)}
                  className={cn(
                    'niuu-flex niuu-w-full niuu-items-start niuu-gap-2 niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-2.5 niuu-py-2 niuu-text-left hover:niuu-bg-bg-tertiary',
                    index > 0 && 'niuu-border-t',
                  )}
                  title={copiedPath === file.path ? 'Copied' : `${file.path} · click to copy`}
                >
                  <span className="niuu-mt-0.5 niuu-inline-flex niuu-h-5 niuu-w-5 niuu-flex-shrink-0 niuu-items-center niuu-justify-center niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-primary">
                    {copiedPath === file.path ? (
                      <Check className="niuu-h-3 niuu-w-3 niuu-text-brand" />
                    ) : (
                      <FilePenLine className="niuu-h-3 niuu-w-3 niuu-text-text-muted" />
                    )}
                  </span>
                  <div className="niuu-min-w-0 niuu-flex-1">
                    <div className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-3">
                      <span className="niuu-truncate niuu-font-mono niuu-text-[11px] niuu-text-text-primary">
                        {truncateLeadingPath(file.path)}
                      </span>
                      <span className="niuu-flex-shrink-0 niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-faint">
                        {file.status}
                      </span>
                    </div>
                    <div className="niuu-mt-0.5 niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
                      +{file.ins} / -{file.del}
                    </div>
                  </div>
                </button>
              ))
            ) : (
              <div className="niuu-text-sm niuu-text-text-muted">No file changes yet.</div>
            )}
          </div>
        </section>

        <section className="niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-p-3">
          <div className="niuu-mb-2.5 niuu-flex niuu-items-center niuu-justify-between">
            <div className="niuu-text-sm niuu-font-medium niuu-text-text-primary">Commits</div>
            <div className="niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
              {chronicle.commits.length}
            </div>
          </div>
          <div className="niuu-flex niuu-flex-col">
            {chronicle.commits.length > 0 ? (
              chronicle.commits.map((commit, index) => (
                <div
                  key={commit.hash}
                  className={cn(
                    'niuu-flex niuu-items-start niuu-gap-2 niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-2.5 niuu-py-2',
                    index > 0 && 'niuu-border-t',
                  )}
                >
                  <span className="niuu-mt-0.5 niuu-inline-flex niuu-h-5 niuu-w-5 niuu-flex-shrink-0 niuu-items-center niuu-justify-center niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-primary">
                    <GitCommitHorizontal className="niuu-h-3 niuu-w-3 niuu-text-text-muted" />
                  </span>
                  <div className="niuu-min-w-0 niuu-flex-1">
                    <div className="niuu-truncate niuu-text-[12px] niuu-text-text-primary">
                      {commit.msg}
                    </div>
                    <div className="niuu-mt-0.5 niuu-flex niuu-items-center niuu-gap-2 niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
                      <span>{commit.hash.slice(0, 8)}</span>
                      <span className="niuu-text-text-faint">•</span>
                      <span>{commit.time}</span>
                    </div>
                  </div>
                </div>
              ))
            ) : (
              <div className="niuu-text-sm niuu-text-text-muted">No commits yet.</div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

interface DiffHunkLine {
  type: 'context' | 'add' | 'remove';
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

interface DiffData {
  filePath: string;
  hunks: DiffHunk[];
}

type DiffBase = 'last-commit' | 'default-branch';

function authHeaders(): Record<string, string> {
  return Object.fromEntries(getAuthHeaders().entries());
}

function useLiveDiffViewer(chatEndpoint: string | null) {
  const apiBase = useMemo(
    () => (chatEndpoint ? wsUrlToHttpBase(chatEndpoint) : null),
    [chatEndpoint],
  );
  const mountedRef = useRef(true);
  const filesAbortRef = useRef<AbortController | null>(null);
  const diffAbortRef = useRef<AbortController | null>(null);
  const [files, setFiles] = useState<SessionFile[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [diff, setDiff] = useState<DiffData | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffError, setDiffError] = useState<Error | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [diffBase, setDiffBaseState] = useState<DiffBase>('last-commit');

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      filesAbortRef.current?.abort();
      diffAbortRef.current?.abort();
    };
  }, []);

  const fetchFiles = useCallback(async () => {
    filesAbortRef.current?.abort();
    if (!apiBase) {
      if (mountedRef.current) {
        setFiles([]);
        setFilesLoading(false);
      }
      return;
    }
    const controller = new AbortController();
    filesAbortRef.current = controller;
    setFilesLoading(true);
    try {
      const params = new URLSearchParams({ base: diffBase });
      const response = await fetch(`${apiBase}/api/diff/files?${params}`, {
        headers: authHeaders(),
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`Failed to fetch diff files: ${response.status}`);
      }
      const data = (await response.json()) as { files?: SessionFile[] };
      if (!mountedRef.current || filesAbortRef.current !== controller) return;
      setFiles(data.files ?? []);
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      if (!mountedRef.current || filesAbortRef.current !== controller) return;
      setFiles([]);
    } finally {
      if (mountedRef.current && filesAbortRef.current === controller) {
        filesAbortRef.current = null;
        setFilesLoading(false);
      }
    }
  }, [apiBase, diffBase]);

  const selectFile = useCallback(
    async (filePath: string) => {
      diffAbortRef.current?.abort();
      if (!apiBase) {
        setSelectedFile(filePath);
        setDiff(null);
        setDiffError(new Error('No session endpoint available'));
        return;
      }
      const controller = new AbortController();
      diffAbortRef.current = controller;
      setSelectedFile(filePath);
      setDiffLoading(true);
      setDiffError(null);
      try {
        const params = new URLSearchParams({ file: filePath, base: diffBase });
        const response = await fetch(`${apiBase}/api/diff?${params}`, {
          headers: authHeaders(),
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`Failed to fetch diff: ${response.status}`);
        }
        if (!mountedRef.current || diffAbortRef.current !== controller) return;
        setDiff((await response.json()) as DiffData);
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        if (!mountedRef.current || diffAbortRef.current !== controller) return;
        setDiff(null);
        setDiffError(error instanceof Error ? error : new Error('Failed to fetch diff'));
      } finally {
        if (mountedRef.current && diffAbortRef.current === controller) {
          diffAbortRef.current = null;
          setDiffLoading(false);
        }
      }
    },
    [apiBase, diffBase],
  );

  const setDiffBase = useCallback((base: DiffBase) => {
    setDiffBaseState(base);
    setSelectedFile(null);
    setDiff(null);
    setDiffError(null);
  }, []);

  useEffect(() => {
    queueMicrotask(() => {
      void fetchFiles();
    });
  }, [fetchFiles]);

  return {
    files,
    filesLoading,
    diff,
    diffLoading,
    diffError,
    selectedFile,
    diffBase,
    selectFile,
    setDiffBase,
  };
}

function diffStatusLetter(status: SessionFile['status']): string {
  return status === 'new' ? 'A' : status === 'mod' ? 'M' : 'D';
}

function diffStatusColor(status: SessionFile['status']): string {
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
  files: SessionFile[];
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
          type="button"
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
    </div>
  );
}

function DiffViewer({
  file,
  diff,
  loading,
  error,
}: {
  file: SessionFile | null;
  diff: DiffData | null;
  loading: boolean;
  error: Error | null;
}) {
  return (
    <div
      className="niuu-flex niuu-h-full niuu-flex-col niuu-overflow-auto"
      data-testid="diff-viewer"
    >
      <div className="niuu-flex niuu-flex-shrink-0 niuu-items-center niuu-gap-2 niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-4 niuu-py-3">
        {file ? (
          <>
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
          </>
        ) : (
          <span className="niuu-font-mono niuu-text-sm niuu-text-text-muted">Diff viewer</span>
        )}
      </div>
      <div className="niuu-flex-1 niuu-overflow-auto niuu-bg-bg-primary">
        {loading ? (
          <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-font-mono niuu-text-sm niuu-text-text-muted">
            Loading diff...
          </div>
        ) : error ? (
          <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-font-mono niuu-text-sm niuu-text-critical">
            Failed to load diff: {error.message}
          </div>
        ) : !file || !diff ? (
          <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-font-mono niuu-text-sm niuu-text-text-muted">
            Select a file to view changes
          </div>
        ) : diff.hunks.length === 0 ? (
          <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-font-mono niuu-text-sm niuu-text-text-muted">
            No changes in this file
          </div>
        ) : (
          diff.hunks.map((hunk, i) => (
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
                      line.type === 'context' && 'niuu-text-text-faint',
                    )}
                  >
                    {line.type === 'add' ? '+' : line.type === 'remove' ? '-' : ' '}
                  </span>
                  <span className="niuu-text-text-primary">{line.content || '\u00A0'}</span>
                </div>
              ))}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function LiveDiffsTab({ chatEndpoint }: { chatEndpoint: string | null }) {
  const {
    files,
    filesLoading,
    diff,
    diffLoading,
    diffError,
    selectedFile,
    diffBase,
    selectFile,
    setDiffBase,
  } = useLiveDiffViewer(chatEndpoint);
  const selected = files.find((file) => file.path === selectedFile) ?? null;

  return (
    <div className="niuu-live-diffs-layout niuu-h-full" data-testid="diffs-tab">
      <div className="niuu-overflow-hidden niuu-border-r niuu-border-border-subtle niuu-bg-bg-secondary">
        <div className="niuu-flex niuu-flex-shrink-0 niuu-items-center niuu-justify-between niuu-border-b niuu-border-border-subtle niuu-bg-bg-primary niuu-px-4 niuu-py-3">
          <div className="niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-muted">
            changed files
          </div>
          <div className="niuu-flex niuu-items-center niuu-gap-1">
            {(['last-commit', 'default-branch'] as const).map((base) => (
              <button
                key={base}
                type="button"
                onClick={() => setDiffBase(base)}
                className={cn(
                  'niuu-rounded-sm niuu-border niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[10px]',
                  diffBase === base
                    ? 'niuu-border-brand niuu-bg-brand/10 niuu-text-brand'
                    : 'niuu-border-border-subtle niuu-text-text-muted hover:niuu-text-text-secondary',
                )}
              >
                {base === 'last-commit' ? 'last commit' : 'default branch'}
              </button>
            ))}
          </div>
        </div>
        {filesLoading ? (
          <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-font-mono niuu-text-sm niuu-text-text-muted">
            Loading files...
          </div>
        ) : (
          <DiffFileList
            files={files}
            selectedPath={selectedFile}
            onSelect={(path) => void selectFile(path)}
          />
        )}
      </div>
      <div className="niuu-overflow-hidden">
        <DiffViewer file={selected} diff={diff} loading={diffLoading} error={diffError} />
      </div>
    </div>
  );
}

export function LiveSessionDetailPage({
  sessionId,
  readOnly = false,
}: {
  sessionId: string;
  readOnly?: boolean;
  initialTab?: SessionTab;
}) {
  return <LiveSessionDetailPageInner key={sessionId} sessionId={sessionId} readOnly={readOnly} />;
}

function LiveSessionDetailPageInner({
  sessionId,
  readOnly = false,
}: {
  sessionId: string;
  readOnly?: boolean;
  initialTab?: SessionTab;
}) {
  const [activeTab, setActiveTab] = useState<SessionTab>('chat');
  const [tabWasManuallySelected, setTabWasManuallySelected] = useState(false);
  const [actionBusy, setActionBusy] = useState<
    'start' | 'stop' | 'archive' | 'restore' | 'delete' | null
  >(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [dismissedHumanGateIds, setDismissedHumanGateIds] = useState<Set<string>>(new Set());
  const [showInternalMessages, setShowInternalMessages] = useState(false);
  const [visibleMessageCount, setVisibleMessageCount] = useState<number | null>(null);
  const volundr = useService<IVolundrService>('volundr');
  const filesystem = useService<IFileSystemPort>('filesystem');
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const sessionQuery = useSessionDetail(sessionId);
  const liveSessionQuery = useQuery({
    queryKey: ['volundr', 'raw-session', sessionId],
    queryFn: () => volundr.getSession(sessionId),
    refetchInterval: 5_000,
  });
  const sessionFeaturesQuery = useQuery({
    queryKey: ['volundr', 'feature-modules', 'session'],
    queryFn: () => volundr.getFeatureModules('session'),
    staleTime: 30_000,
  });
  const featurePrefsQuery = useQuery({
    queryKey: ['volundr', 'feature-prefs', 'session'],
    queryFn: () => volundr.getUserFeaturePreferences(),
    staleTime: 30_000,
  });
  const liveSession = liveSessionQuery.data;
  const sessionStatus = liveSession?.status ?? null;
  const isRunning = sessionStatus === 'running';
  const chatEndpoint = normalizeSessionUrl(liveSession?.chatEndpoint ?? null);
  const isReady = isRunning && Boolean(chatEndpoint);
  const canReplayTranscript =
    readOnly ||
    sessionStatus === 'stopped' ||
    sessionStatus === 'archived' ||
    sessionStatus === 'failed' ||
    sessionStatus === 'error';
  const terminalUrl = deriveTerminalWsUrl(chatEndpoint);
  const chat = useSkuldChat(chatEndpoint);
  const workflowGatesQuery = useQuery({
    queryKey: ['volundr', 'workflow-gates', sessionId],
    queryFn: () => volundr.getWorkflowGates(sessionId),
    enabled: Boolean(sessionId) && isRunning,
    refetchInterval: isRunning ? 5_000 : false,
  });
  const transcriptQuery = useQuery({
    queryKey: ['volundr', 'conversation-history', sessionId],
    queryFn: () => volundr.getConversationHistory(sessionId),
    enabled: Boolean(sessionId) && canReplayTranscript,
    staleTime: 5_000,
  });
  const sessionName = liveSession?.name ?? sessionQuery.data?.personaName ?? sessionId;
  const sessionTrackerIssue = liveSession?.trackerIssue;
  const sessionHandle = useMemo(() => {
    const rawHandle = sessionQuery.data?.ravnId?.trim();
    if (!rawHandle) return null;
    const normalizedHandle = rawHandle.toLowerCase();
    if (
      normalizedHandle === sessionId.toLowerCase() ||
      normalizedHandle === sessionName.trim().toLowerCase() ||
      normalizedHandle === sessionTrackerIssue?.identifier.trim().toLowerCase()
    ) {
      return null;
    }
    return rawHandle;
  }, [sessionId, sessionName, sessionQuery.data?.ravnId, sessionTrackerIssue?.identifier]);
  const telemetryRunLabel = useMemo(() => {
    const domainRunId = sessionQuery.data?.ravnId?.trim();
    if (looksLikeRunLabel(sessionHandle)) return sessionHandle;
    if (looksLikeRunLabel(domainRunId)) return domainRunId;
    return sessionName;
  }, [sessionHandle, sessionName, sessionQuery.data?.ravnId]);
  const forgeBadgeLabel = useMemo(() => {
    const clusterName = sessionQuery.data?.clusterName?.trim();
    if (clusterName) return clusterName;
    const clusterId = sessionQuery.data?.clusterId?.trim();
    if (clusterId && clusterId !== 'local') return normalizeForgeBadgeLabel(clusterId);
    const instanceName = liveSession?.instanceName?.trim();
    if (instanceName) return normalizeForgeBadgeLabel(instanceName);
    const instanceId = liveSession?.instanceId?.trim();
    if (instanceId) return normalizeForgeBadgeLabel(instanceId);
    return null;
  }, [
    liveSession?.instanceId,
    liveSession?.instanceName,
    sessionQuery.data?.clusterId,
    sessionQuery.data?.clusterName,
  ]);
  const sessionFeatures = useMemo(
    () => sessionFeaturesQuery.data ?? [],
    [sessionFeaturesQuery.data],
  );
  const featurePrefs = useMemo(() => featurePrefsQuery.data ?? [], [featurePrefsQuery.data]);
  const transcriptTurns = useMemo(() => transcriptQuery.data?.turns ?? [], [transcriptQuery.data]);
  const replayMessages = useMemo(() => transformTurns(transcriptTurns), [transcriptTurns]);
  const replayParticipants = useMemo(
    () => participantsFromTurns(transcriptTurns),
    [transcriptTurns],
  );
  const replayMeshEvents = useMemo(() => meshEventsFromTurns(transcriptTurns), [transcriptTurns]);
  const workflowGates = useMemo(() => workflowGatesQuery.data ?? [], [workflowGatesQuery.data]);
  const activeHumanGate = useMemo(() => {
    const nativeGate = workflowGates.find(
      (gate) =>
        gate.status === 'pending' &&
        gate.pending_behavior === 'help_needed' &&
        !dismissedHumanGateIds.has(gate.id),
    );
    if (nativeGate) {
      return {
        source: 'native' as const,
        eventId: nativeGate.id,
        gate: nativeGate,
        summary:
          nativeGate.summary ||
          `${nativeGate.label} is waiting for human approval before the workflow can continue.`,
        reason: nativeGate.condition || undefined,
        recommendation: 'Approve to continue or request changes to send the workflow back.',
      };
    }

    const latest = [...chat.meshEvents]
      .reverse()
      .find(
        (event): event is MeshNotificationEvent =>
          event.type === 'notification' && event.notificationType === 'help_needed',
      );
    if (!latest) return null;
    const eventId =
      latest.id ||
      [
        latest.participantId,
        latest.type,
        latest.notificationType,
        latest.timestamp.toISOString(),
      ].join(':');
    if (dismissedHumanGateIds.has(eventId)) return null;
    const participant = chat.participants.get(latest.participantId);
    if (!participant) return null;
    return {
      source: 'legacy' as const,
      eventId,
      event: {
        summary: latest.summary || 'Workflow gate is waiting for human approval.',
        reason: latest.reason,
        recommendation: latest.recommendation,
        persona: latest.persona,
      },
      participant: {
        peerId: participant.peerId,
        displayName: participant.displayName,
        persona: participant.persona,
        participantType: participant.participantType,
      },
    };
  }, [chat.meshEvents, chat.participants, dismissedHumanGateIds, workflowGates]);
  const sessionDetail = sessionQuery.data;
  const uptimeValue = formatElapsedSince(sessionDetail?.startedAt);
  const costValue =
    sessionDetail?.costCents != null ? formatCurrencyCents(sessionDetail.costCents) : null;
  const trailingMetric = useMemo(() => {
    if (costValue) {
      return { label: 'Cost', value: costValue };
    }
    if (forgeBadgeLabel) {
      return null;
    }
    return { label: 'Forge', value: sessionForgeLabel(liveSession) };
  }, [costValue, forgeBadgeLabel, liveSession]);
  const tabCounts: Partial<Record<SessionTab, number>> = {
    chat: liveSession?.messageCount || replayMessages.length || undefined,
    diffs: fileChangeCount(sessionDetail?.files),
    chronicles: sessionDetail?.events.length ? sessionDetail.events.length : undefined,
  };
  const headerMessageCount = useMemo(() => {
    if (visibleMessageCount != null) return visibleMessageCount;
    if (canReplayTranscript) return replayMessages.length;
    return liveSession?.messageCount ?? 0;
  }, [canReplayTranscript, liveSession?.messageCount, replayMessages.length, visibleMessageCount]);
  const isSessionConnected = isReady && chat.connected;

  const tabs = useMemo(() => {
    const prefMap = new Map(featurePrefs.map((pref) => [pref.featureKey, pref]));
    const visible = ALL_TABS.filter((tab) => {
      if (tab.id === 'diffs') return true;
      if (tab.id === 'telemetry') return true;
      if (tab.id === 'terminal') {
        const pref = prefMap.get(tab.id);
        if (pref && !pref.visible) return false;
        const feature = sessionFeatures.find((candidate) => candidate.key === tab.id);
        return Boolean(feature?.enabled || terminalUrl);
      }
      const feature = sessionFeatures.find((candidate) => candidate.key === tab.id);
      if (!feature?.enabled) return false;
      const pref = prefMap.get(tab.id);
      if (pref && !pref.visible) return false;
      return true;
    });

    visible.sort((left, right) => {
      const leftFeature = sessionFeatures.find((feature) => feature.key === left.id);
      const rightFeature = sessionFeatures.find((feature) => feature.key === right.id);
      const leftPref = prefMap.get(left.id);
      const rightPref = prefMap.get(right.id);
      const leftOrder =
        FIXED_TAB_ORDER[left.id] ?? (leftPref ? leftPref.sortOrder : (leftFeature?.order ?? 0));
      const rightOrder =
        FIXED_TAB_ORDER[right.id] ?? (rightPref ? rightPref.sortOrder : (rightFeature?.order ?? 0));
      return leftOrder - rightOrder;
    });

    return visible;
  }, [featurePrefs, sessionFeatures, terminalUrl]);

  const handleToggleInternalMessages = useCallback(() => {
    const next = !showInternalMessages;
    setShowInternalMessages(next);
    if (isReady) {
      chat.sendSetInternalVisibility(next);
    }
  }, [chat, isReady, showInternalMessages]);

  const handleHumanGateReply = useCallback(
    (decision: 'APPROVE' | 'CHANGES_REQUESTED', notes: string) => {
      if (!activeHumanGate) return;
      setDismissedHumanGateIds((current) => {
        const next = new Set(current);
        next.add(activeHumanGate.eventId);
        return next;
      });
      if (activeHumanGate.source === 'native') {
        void volundr
          .resolveWorkflowGate(sessionId, activeHumanGate.gate.id, {
            decision,
            notes,
            source: 'human',
          })
          .then(() =>
            queryClient.invalidateQueries({
              queryKey: ['volundr', 'workflow-gates', sessionId],
            }),
          );
        return;
      }
      void chat.sendDirectedMessages(
        [activeHumanGate.participant],
        notes ? `${decision}\n\n${notes}` : decision,
        [],
      );
    },
    [activeHumanGate, chat, queryClient, sessionId, volundr],
  );

  const resolvedActiveTab =
    !tabWasManuallySelected && tabs.some((tab) => tab.id === 'chat')
      ? 'chat'
      : tabs.some((tab) => tab.id === activeTab)
        ? activeTab
        : (tabs.find((tab) => tab.id === 'chat')?.id ?? tabs[0]?.id ?? 'chat');

  const evaluatePermissionAutoApproval = useCallback(
    (permission: (typeof chat.pendingPermissions)[number]) =>
      volundr.evaluatePermissionAutoApproval(
        sessionId,
        buildPermissionAutoApprovalRequest(permission),
      ),
    [chat, sessionId, volundr],
  );

  const permissionRenderer = useCallback(
    (permissions: typeof chat.pendingPermissions, onRespond: typeof chat.respondToPermission) => (
      <PermissionApprovalPanel
        permissions={permissions}
        onRespond={onRespond}
        evaluateAutoApproval={evaluatePermissionAutoApproval}
      />
    ),
    [chat, evaluatePermissionAutoApproval],
  );

  async function refreshSessionData() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['volundr', 'raw-session', sessionId] }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'raw-session', 'logs', sessionId] }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'conversation-history', sessionId] }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'domain-session', sessionId] }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'domain-sessions'] }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'history'] }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'filetree', sessionId] }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'session-list'] }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'stats'] }),
    ]);
  }

  async function handleStopSession() {
    if (!liveSession || actionBusy) return;
    setActionBusy('stop');
    try {
      await volundr.stopSession(liveSession.id);
      await refreshSessionData();
    } finally {
      setActionBusy(null);
    }
  }

  async function handleResumeSession() {
    if (!liveSession || actionBusy) return;
    setActionBusy('start');
    try {
      await volundr.resumeSession(liveSession.id);
      await refreshSessionData();
    } finally {
      setActionBusy(null);
    }
  }

  async function handleDeleteSession(cleanup: string[]) {
    if (!liveSession || actionBusy) return;
    setActionBusy('delete');
    try {
      await Promise.all([
        queryClient.cancelQueries({ queryKey: ['volundr', 'raw-session', sessionId] }),
        queryClient.cancelQueries({ queryKey: ['volundr', 'raw-session', 'logs', sessionId] }),
        queryClient.cancelQueries({ queryKey: ['volundr', 'conversation-history', sessionId] }),
        queryClient.cancelQueries({ queryKey: ['volundr', 'domain-session', sessionId] }),
        queryClient.cancelQueries({ queryKey: ['volundr', 'filetree', sessionId] }),
        queryClient.cancelQueries({ queryKey: ['volundr', 'workflow-gates', sessionId] }),
      ]);
      await volundr.deleteSession(liveSession.id, cleanup);
      setDeleteDialogOpen(false);
      queryClient.removeQueries({ queryKey: ['volundr', 'raw-session', sessionId] });
      queryClient.removeQueries({ queryKey: ['volundr', 'raw-session', 'logs', sessionId] });
      queryClient.removeQueries({ queryKey: ['volundr', 'conversation-history', sessionId] });
      queryClient.removeQueries({ queryKey: ['volundr', 'domain-session', sessionId] });
      queryClient.removeQueries({ queryKey: ['volundr', 'filetree', sessionId] });
      queryClient.removeQueries({ queryKey: ['volundr', 'workflow-gates', sessionId] });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['volundr', 'domain-sessions'] }),
        queryClient.invalidateQueries({ queryKey: ['volundr', 'history'] }),
        queryClient.invalidateQueries({ queryKey: ['volundr', 'session-list'] }),
        queryClient.invalidateQueries({ queryKey: ['volundr', 'stats'] }),
      ]);
      await navigate({ to: '/volundr/sessions', replace: true });
    } finally {
      setActionBusy(null);
    }
  }

  async function handleArchiveSession() {
    if (!liveSession || actionBusy) return;
    setActionBusy('archive');
    try {
      await volundr.archiveSession(liveSession.id);
      await refreshSessionData();
    } finally {
      setActionBusy(null);
    }
  }

  async function handleRestoreSession() {
    if (!liveSession || actionBusy) return;
    setActionBusy('restore');
    try {
      await volundr.restoreSession(liveSession.id);
      await refreshSessionData();
    } finally {
      setActionBusy(null);
    }
  }

  if (
    sessionQuery.isLoading ||
    liveSessionQuery.isLoading ||
    sessionFeaturesQuery.isLoading ||
    featurePrefsQuery.isLoading
  ) {
    return <LoadingState label="Loading session…" />;
  }

  if (
    sessionQuery.isError ||
    liveSessionQuery.isError ||
    sessionFeaturesQuery.isError ||
    featurePrefsQuery.isError
  ) {
    const error =
      sessionQuery.error ??
      liveSessionQuery.error ??
      sessionFeaturesQuery.error ??
      featurePrefsQuery.error;
    return (
      <ErrorState
        title="Failed to load session"
        message={error instanceof Error ? error.message : 'Unknown error'}
      />
    );
  }

  return (
    <div className="niuu-flex niuu-h-full niuu-flex-col" data-testid="live-session-detail-page">
      <div className="niuu-live-session__chrome">
        <div className="niuu-live-session__header">
          <div className="niuu-live-session__title-group">
            <span
              className={cn(
                'niuu-live-session__status-dot',
                isSessionConnected
                  ? 'niuu-live-session__status-dot--connected'
                  : 'niuu-live-session__status-dot--disconnected',
              )}
            />
            <div className="niuu-live-session__identity">
              <span className="niuu-live-session__title">{sessionName}</span>
              {sessionHandle ? (
                <span className="niuu-live-session__handle" title={sessionHandle}>
                  {sessionHandle}
                </span>
              ) : null}
              <SessionIdChip sessionId={sessionId} />
            </div>
            {sessionTrackerIssue ? (
              <>
                <HeaderDivider />
                <TicketLink issue={sessionTrackerIssue} />
              </>
            ) : null}
            {liveSession?.source ? (
              <>
                <HeaderDivider />
                <SourceMeta session={liveSession} />
              </>
            ) : null}
            {forgeBadgeLabel ? (
              <>
                <HeaderDivider />
                <SessionForgeBadge label={forgeBadgeLabel} />
              </>
            ) : null}
            {readOnly ? (
              <>
                <HeaderDivider />
                <span className="niuu-live-session__archived-badge">Archived</span>
              </>
            ) : null}
          </div>

          <div className="niuu-live-session__metrics-row" data-testid="session-stats">
            <HeaderMetric label="Uptime" value={uptimeValue} />
            <HeaderDivider />
            <HeaderMetric label="Msgs" value={formatCount(headerMessageCount)} />
            <HeaderDivider />
            <HeaderMetric label="Tokens" value={formatCount(liveSession?.tokensUsed ?? 0)} />
            {trailingMetric ? (
              <>
                <HeaderDivider />
                <HeaderMetric label={trailingMetric.label} value={trailingMetric.value} />
              </>
            ) : null}
          </div>
        </div>

        <div className="niuu-live-session__tabs-row">
          <div className="niuu-live-session__tabs" role="tablist" aria-label="Session tabs">
            {tabs.map((tab) =>
              (() => {
                const TabIcon = tab.icon;
                return (
                  <button
                    key={tab.id}
                    id={`tab-${tab.id}`}
                    role="tab"
                    aria-selected={resolvedActiveTab === tab.id}
                    onClick={() => {
                      setTabWasManuallySelected(true);
                      setActiveTab(tab.id);
                    }}
                    className={cn(
                      'niuu-live-session__tab',
                      resolvedActiveTab === tab.id && 'niuu-live-session__tab--active',
                    )}
                  >
                    <TabIcon className="niuu-live-session__tab-icon" />
                    <span>{tab.label}</span>
                    <TabCountBadge count={tabCounts[tab.id]} />
                  </button>
                );
              })(),
            )}
          </div>
          <div className="niuu-live-session__toolbar">
            <SessionToolbarButton
              icon={showInternalMessages ? Eye : EyeOff}
              title={
                showInternalMessages ? 'Hide tool calls and results' : 'Show tool calls and results'
              }
              active={showInternalMessages}
              onClick={handleToggleInternalMessages}
            />
            {liveSession &&
              !readOnly &&
              (liveSession.status === 'running' ? (
                <SessionToolbarButton
                  icon={Square}
                  title="Stop session"
                  onClick={() => void handleStopSession()}
                  disabled={actionBusy !== null}
                  tone="critical"
                />
              ) : liveSession.status === 'stopped' ? (
                <SessionToolbarButton
                  icon={Play}
                  title="Start session"
                  onClick={() => void handleResumeSession()}
                  disabled={actionBusy !== null}
                  tone="brand"
                />
              ) : liveSession.status === 'archived' ? null : (
                <SessionToolbarButton
                  icon={Play}
                  title="Start session"
                  onClick={() => void handleResumeSession()}
                  disabled={actionBusy !== null}
                  tone="brand"
                />
              ))}
            {!readOnly && liveSession && liveSession.status !== 'archived' && (
              <SessionToolbarButton
                icon={Archive}
                title="Archive session"
                onClick={() => void handleArchiveSession()}
                disabled={actionBusy !== null}
              />
            )}
            {liveSession?.status === 'archived' ? (
              <SessionToolbarButton
                icon={RotateCcw}
                title="Restore archived session"
                onClick={() => void handleRestoreSession()}
                disabled={actionBusy !== null}
                tone="brand"
              />
            ) : null}
            {!readOnly && (
              <SessionToolbarButton
                icon={Trash2}
                title="Delete session"
                onClick={() => setDeleteDialogOpen(true)}
                disabled={actionBusy !== null}
                tone="critical"
              />
            )}
          </div>
        </div>
      </div>

      <div className="niuu-min-h-0 niuu-flex-1 niuu-overflow-hidden">
        {resolvedActiveTab === 'chat' && (
          <div role="tabpanel" className="niuu-flex niuu-h-full niuu-min-h-0 niuu-flex-col">
            {isReady && chatEndpoint ? (
              <>
                {activeHumanGate && (
                  <WorkflowHumanGateCard
                    title={
                      activeHumanGate.source === 'native'
                        ? activeHumanGate.gate.label
                        : activeHumanGate.participant.displayName ||
                          activeHumanGate.participant.persona ||
                          activeHumanGate.event.persona ||
                          'Workflow gate'
                    }
                    summary={
                      activeHumanGate.source === 'native'
                        ? activeHumanGate.summary
                        : activeHumanGate.event.summary || 'Workflow gate'
                    }
                    reason={
                      activeHumanGate.source === 'native'
                        ? activeHumanGate.reason
                        : activeHumanGate.event.reason
                    }
                    recommendation={
                      activeHumanGate.source === 'native'
                        ? activeHumanGate.recommendation
                        : activeHumanGate.event.recommendation
                    }
                    onApprove={(notes) => handleHumanGateReply('APPROVE', notes)}
                    onRequestChanges={(notes) => handleHumanGateReply('CHANGES_REQUESTED', notes)}
                  />
                )}
                <SessionChat
                  className="niuu-h-full"
                  showToolbar={false}
                  showInternalToggle={false}
                  internalVisibility={showInternalMessages}
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
                  capabilities={chat.capabilities}
                  chatEndpoint={chatEndpoint}
                  sessionName={sessionName}
                  onSend={chat.sendMessage}
                  onSendDirected={chat.sendDirectedMessages}
                  onStop={chat.sendInterrupt}
                  onClear={chat.clearMessages}
                  onSetInternalVisibility={chat.sendSetInternalVisibility}
                  onSetModel={chat.sendSetModel}
                  onSetThinkingTokens={chat.sendSetThinkingTokens}
                  onRewindFiles={chat.sendRewindFiles}
                  onPermissionRespond={chat.respondToPermission}
                  onMessageCountChange={setVisibleMessageCount}
                  renderPermissions={permissionRenderer}
                />
              </>
            ) : canReplayTranscript && transcriptQuery.isLoading ? (
              <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-text-muted">
                Loading saved transcript…
              </div>
            ) : canReplayTranscript && transcriptQuery.isError ? (
              <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-critical">
                Failed to load saved transcript.
              </div>
            ) : canReplayTranscript && replayMessages.length > 0 ? (
              <SessionChat
                className="niuu-h-full"
                showToolbar={false}
                showInternalToggle={false}
                internalVisibility={showInternalMessages}
                messages={replayMessages}
                connected={false}
                historyLoaded={!transcriptQuery.isLoading}
                participants={replayParticipants}
                meshEvents={replayMeshEvents}
                sessionName={sessionName}
                onMessageCountChange={setVisibleMessageCount}
                onSend={() => {}}
                onStop={() => {}}
              />
            ) : isSessionBooting(sessionStatus) ? (
              <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-text-muted">
                Session is starting…
              </div>
            ) : canReplayTranscript ? (
              <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-text-muted">
                No saved transcript yet.
              </div>
            ) : (
              <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-text-muted">
                Start the session to chat.
              </div>
            )}
          </div>
        )}

        {resolvedActiveTab === 'terminal' && (
          <div role="tabpanel" className="niuu-h-full niuu-min-h-0">
            {isReady ? (
              <SessionTerminalLive url={terminalUrl} readOnly={readOnly} />
            ) : isSessionBooting(sessionStatus) ? (
              <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-text-muted">
                Session is starting…
              </div>
            ) : (
              <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-text-muted">
                Start the session to access terminal.
              </div>
            )}
          </div>
        )}

        {resolvedActiveTab === 'diffs' && (
          <div role="tabpanel" className="niuu-h-full niuu-min-h-0">
            <LiveDiffsTab chatEndpoint={chatEndpoint} />
          </div>
        )}

        {resolvedActiveTab === 'files' && (
          <div role="tabpanel" className="niuu-h-full niuu-min-h-0">
            <SessionFilesWorkspace sessionId={sessionId} filesystem={filesystem} />
          </div>
        )}

        {resolvedActiveTab === 'chronicles' && (
          <div role="tabpanel" className="niuu-h-full niuu-min-h-0">
            <LiveChroniclesTab
              sessionId={sessionId}
              sessionStatus={sessionStatus}
              session={liveSession ?? null}
              volundr={volundr}
            />
          </div>
        )}

        {resolvedActiveTab === 'telemetry' && (
          <div
            role="tabpanel"
            className="niuu-live-telemetry-panel niuu-h-full niuu-min-h-0 niuu-overflow-auto"
          >
            <TelemetryTab
              sessionId={sessionId}
              session={liveSession ?? null}
              runLabel={telemetryRunLabel ?? sessionName ?? 'session'}
              volundr={volundr}
              isRunning={isRunning}
            />
          </div>
        )}

        {resolvedActiveTab === 'logs' && (
          <div role="tabpanel" className="niuu-h-full niuu-min-h-0">
            <LiveLogsTab sessionId={sessionId} volundr={volundr} />
          </div>
        )}
      </div>

      <DeleteSessionDialog
        open={deleteDialogOpen}
        session={liveSession ?? null}
        onClose={() => setDeleteDialogOpen(false)}
        onConfirm={(cleanup) => void handleDeleteSession(cleanup)}
        busy={actionBusy === 'delete'}
      />
    </div>
  );
}
