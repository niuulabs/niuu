import { useCallback, useContext, useEffect, useMemo, useReducer, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { RenameSession } from './RenameSession';
import { AssignSessionProject } from './AssignSessionProject';
import { PinSession } from './PinSession';
import { SessionActivityElapsed } from './SessionActivityElapsed';
import { SessionStartupProgress } from './SessionStartupProgress';
import { useNavigate } from '@tanstack/react-router';
import { useOptionalService, useService } from '@niuulabs/plugin-sdk';
import { getAuthHeaders } from '@niuulabs/query';
import {
  Dialog,
  DialogContent,
  ConversationLink,
  GateDecision,
  ReviewBar,
  FilesPane,
  SessionDiffs,
  SessionFilesWorkspace,
  SessionResources,
  presentedFilesIn,
  formatReviewMessage,
  gateNotes,
  useReviewComments,
  type ReviewComment,
  ErrorState,
  LoadingState,
  SessionChat,
  type ISessionHistoryLocator,
  type ChatMessage,
  ChatConnectionsContext,
  SecretRequestsContext,
  type SecretRequestsPort,
  ChatDisplayControls,
  SessionLinksProvider,
  SidebarResizer,
  QuickOpenFiles,
  fetchSessionDiffFiles,
  resolveSessionResource,
  roomWorkers,
  useResizableSidebar,
  type FileEntry,
  type MeshNotificationEvent,
  type Worker,
  cn,
} from '@niuulabs/ui';
import {
  Archive,
  AlertTriangle,
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Columns2,
  CornerDownRight,
  Eye,
  FileCode2,
  FileDiff,
  FilePenLine,
  FileText,
  Folder,
  FolderOpen,
  GitBranch,
  GitCommitHorizontal,
  Globe,
  KeyRound,
  Mail,
  MailOpen,
  MessageCircleReply,
  MessageSquareText,
  PanelRight,
  Play,
  RotateCcw,
  ScrollText,
  Sparkles,
  Square,
  SquareTerminal,
  Trash2,
} from 'lucide-react';
import type { IVolundrService } from '../ports/IVolundrService';
import type { FileTreeNode, IFileSystemPort } from '../ports/IFileSystemPort';
import type {
  SessionChronicle,
  VolundrAggregatedLog,
  VolundrLogParticipant,
  VolundrSession,
  VolundrSessionTrace,
  VolundrSessionTraceSpan,
  VolundrSessionTraceSummary,
} from '../models/volundr.model';
import { useSessionDetail, useSessionList } from './hooks/useSessionStore';
import { coordinationOf, isCoordinationRole, leaderTag } from '../domain/projectTree';
import { withRunMembers } from '../domain/runRoom';
import { buildSessionLinks } from './sessions/sessionLinks';
import { CoordinatorTag, WorkerOf } from './detail/Coordination';
import { DecisionDock } from './detail/DecisionDock';
import { RightDock, type RightDockTab } from './detail/RightDock';
import {
  EMPTY_DOCK,
  activeDockPane,
  dockPaneId,
  dockReducer,
  type DockPane,
} from './detail/dockPanes';
import { TerminalDock } from './detail/TerminalDock';
import { FilePreviewPane } from './detail/FilePreviewPane';
import { LivePreviewPane } from './detail/LivePreviewPane';
import { LIVE_PREVIEW_DISABLED, findLocalUrls } from '../domain/livePreview';
import { SideChatPane, sideChatLabel } from './detail/SideChatPane';
import { compactDuration, useMinuteClock } from './sessions/timeFact';
import { useElementHeight, useElementWidth } from './detail/useElementWidth';
import { useSessionRead } from './hooks/useSessionRead';
import { useMediaQuery } from './hooks/useMediaQuery';

/** Matches the width below which SessionAttentionPanel.css drops the panel's column. */
const PANEL_OVERLAY_QUERY = '(max-width: 1100px)';
import {
  meshEventsFromTurns,
  participantsFromTurns,
  transformTurns,
  useSkuldChat,
} from './hooks/useSkuldChat';
import { deriveTerminalWsUrl, normalizeSessionUrl } from './liveSessionTransport';
import { PermissionApprovalPanel } from './PermissionApprovalPanel';
import { buildPermissionAutoApprovalRequest } from './permissionAutoApproval';
import { SessionTerminalLive } from './SessionTerminalLive';
import { StructuredLogViewer } from './components/StructuredLogViewer';
import { LinkedText } from './LinkedText';
import { errorText } from './errorText';
import './LiveSessionDetailPage.css';
import { useSessionTabs } from './ForgeSessionSettings';
import { useForgePreference } from './useForgePreference';
import { SessionEnvironment } from './SessionEnvironment';
import { SessionRuntimeVersion } from './SessionRuntimeVersion';
import { modelLabel } from '@niuulabs/domain';
import {
  buildAttentionItems,
  resolveChecklist,
  type SessionGateSummary,
} from '../domain/sessionAttention';
import { sessionHarness } from '../domain/session';
import { SessionAttentionPanel, sessionPanelHasContent } from './SessionAttentionPanel';
import {
  CONVERSATION_MIN_HEIGHT,
  DOCK_WIDTH,
  SESSION_STATE_LABELS,
  TERMINAL_HEIGHT,
  TRANSCRIPT_MIN_WIDTH,
} from './sessions/presentation';
import { ContextMeter } from './detail/ContextMeter';
import { SessionMoreMenu, type MoreMenuItem } from './detail/SessionMoreMenu';
import { Subline } from './detail/Subline';
import { ForwardMessageDialog, type ForwardedNotice } from './detail/ForwardMessageDialog';
import {
  contextMeterData,
  isSessionWorking,
  sessionInfoRows,
  stateTone,
} from './detail/sessionHeaderModel';

// The terminal is no longer a tab: it docks under the conversation, as Lexi's does.
export type LiveSessionTab = 'chat' | 'diffs' | 'files' | 'chronicles' | 'telemetry' | 'logs';

type SessionTab = LiveSessionTab;

/** Statuses that change only through an action or a pushed state change. */
const SETTLED_RAW_STATUSES: ReadonlySet<VolundrSession['status']> = new Set([
  'stopped',
  'archived',
  'failed',
  'error',
]);

const ALL_TABS: Array<{ id: SessionTab; label: string; icon: typeof MessageSquareText }> = [
  { id: 'chat', label: 'Chat', icon: MessageSquareText },
  { id: 'diffs', label: 'Diffs', icon: FileDiff },
  { id: 'files', label: 'Files', icon: FolderOpen },
  { id: 'chronicles', label: 'Chronicle', icon: ScrollText },
  { id: 'telemetry', label: 'Telemetry', icon: Sparkles },
  { id: 'logs', label: 'Logs', icon: FileCode2 },
];

export function normalizeWorkReturnTo(value?: string): string | null {
  if (!value?.startsWith('/')) return null;
  try {
    const target = new URL(value, 'https://niuu.local');
    if (target.origin !== 'https://niuu.local') return null;
    if (target.pathname !== '/ting/work' && !target.pathname.startsWith('/ting/work/')) return null;
    return `${target.pathname}${target.search}${target.hash}`;
  } catch {
    return null;
  }
}

export function isSessionBooting(status: string | null | undefined): boolean {
  return status === 'created' || status === 'starting' || status === 'provisioning';
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
  comments,
  onApprove,
  onRequestChanges,
}: {
  title: string;
  summary: string;
  reason?: string;
  recommendation?: string;
  comments: readonly ReviewComment[];
  onApprove: (notes: string) => void;
  onRequestChanges: (notes: string) => void;
}) {
  const [note, setNote] = useState('');
  const facts = [
    ...(reason ? [{ label: 'Reason', value: reason }] : []),
    ...(recommendation ? [{ label: 'Recommendation', value: recommendation }] : []),
  ];
  function answer(send: (notes: string) => void) {
    send(gateNotes(note, comments));
    setNote('');
  }

  return (
    <div className="niuu:m-3 niuu:mb-0">
      <GateDecision
        eyebrow="Your decision"
        title={title}
        summary={summary}
        facts={facts}
        hint="This reply resolves the pending workflow gate and resumes the workflow."
        note={note}
        onNoteChange={setNote}
        noteLabel="Reply Notes"
        notePlaceholder="Optional context for the gate decision."
        commentCount={comments.length}
        onApprove={() => answer(onApprove)}
        onRequestChanges={() => answer(onRequestChanges)}
      />
    </div>
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
  roots: TelemetrySpanNode[];
  nodeById: Map<string, TelemetrySpanNode>;
} {
  const nodeById = new Map<string, TelemetrySpanNode>();
  for (const span of trace.spans) {
    nodeById.set(span.id, { span, children: [] });
  }
  const roots: TelemetrySpanNode[] = [];
  for (const span of trace.spans) {
    const node = nodeById.get(span.id);
    if (!node) continue;
    const parentNode = span.parentSpanId ? nodeById.get(span.parentSpanId) : undefined;
    if (parentNode) {
      parentNode.children.push(node);
    } else {
      roots.push(node);
    }
  }
  for (const node of nodeById.values()) {
    node.children.sort(
      (left, right) =>
        new Date(left.span.startedAt).getTime() - new Date(right.span.startedAt).getTime(),
    );
  }
  return { roots, nodeById };
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

function spanStartedAtMs(span: VolundrSessionTraceSpan): number {
  const value = new Date(span.startedAt).getTime();
  return Number.isFinite(value) ? value : 0;
}

function isSerialTelemetryTaskSpan(span: VolundrSessionTraceSpan): boolean {
  return span.kind === 'tool.call' || span.kind === 'terminal.command';
}

export function telemetryTaskDurationMs(
  span: VolundrSessionTraceSpan,
  nextSiblingStartedAtMs?: number | null,
): number {
  const rawDurationMs = span.durationMs ?? 0;
  if (
    !isSerialTelemetryTaskSpan(span) ||
    nextSiblingStartedAtMs == null ||
    !Number.isFinite(nextSiblingStartedAtMs)
  ) {
    return rawDurationMs;
  }

  const startedAtMs = spanStartedAtMs(span);
  if (nextSiblingStartedAtMs <= startedAtMs) return rawDurationMs;
  return Math.min(rawDurationMs, nextSiblingStartedAtMs - startedAtMs);
}

export function buildTelemetryTimelineRows(trace: VolundrSessionTrace): TelemetryTimelineRow[] {
  const { roots } = buildTelemetrySpanTree(trace);
  if (roots.length === 0) return [];
  const rootStart = trace.startedAt
    ? new Date(trace.startedAt).getTime()
    : Math.min(...trace.spans.map(spanStartedAtMs));
  const totalDuration = trace.durationMs ?? 0;
  // Lifecycle spans describe individual broker attempts, not the whole session.
  // Keep their stages together, plus spans whose parent was never persisted.
  return roots
    .flatMap((node) => (node.span.kind === 'session.lifecycle' ? node.children : [node]))
    .sort((left, right) => spanStartedAtMs(left.span) - spanStartedAtMs(right.span))
    .map(({ span, children }) => {
      const startOffsetMs = Math.max(0, new Date(span.startedAt).getTime() - rootStart);
      const durationMs = span.durationMs ?? 0;
      const percentOfTotal = totalDuration > 0 ? Math.round((durationMs / totalDuration) * 100) : 0;
      const tone = timelineRowTone(span);
      const childSpans = children.map((child) => child.span);
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
  const { nodeById } = buildTelemetrySpanTree(trace);
  const turnNodes = [...nodeById.values()].filter((node) => node.span.kind.startsWith('turn.'));
  if (turnNodes.length === 0) return [];

  // Exclude aggregate turns individually; another attempt can have direct turns
  // even when this attempt nests its turns under a coordinator.
  const candidateNodes = turnNodes.filter(
    (node) => !node.children.some((child) => child.span.kind.startsWith('turn.')),
  );

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
  const selectedTurnId = turns.find((turn) => turn.id === selectedTurnIdState)?.id ?? null;

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

function TelemetryTaskRow({
  node,
  depth,
  stageStartedAtMs,
  stageDurationMs,
  nextSiblingStartedAtMs,
}: {
  node: TelemetrySpanNode;
  depth: number;
  stageStartedAtMs: number;
  stageDurationMs: number;
  nextSiblingStartedAtMs?: number | null;
}) {
  const tone = timelineRowTone(node.span);
  const durationMs = telemetryTaskDurationMs(node.span, nextSiblingStartedAtMs);
  const safeStageDurationMs = Math.max(stageDurationMs, 1);
  const taskStartOffsetMs = Math.max(0, spanStartedAtMs(node.span) - stageStartedAtMs);
  const clampedTaskStartOffsetMs = Math.min(taskStartOffsetMs, safeStageDurationMs);
  const clampedTaskDurationMs = Math.max(
    0,
    Math.min(clampedTaskStartOffsetMs + durationMs, safeStageDurationMs) - clampedTaskStartOffsetMs,
  );
  const taskLeftPercent = (clampedTaskStartOffsetMs / safeStageDurationMs) * 100;
  const taskWidthPercent = Math.max((clampedTaskDurationMs / safeStageDurationMs) * 100, 1);
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
          <div className="niuu-live-telemetry__breakdown-task-track" aria-hidden="true">
            <span
              data-testid={`telemetry-breakdown-task-segment-${node.span.id}`}
              className={cn(
                'niuu-live-telemetry__breakdown-task-segment',
                `niuu-live-telemetry__breakdown-task-segment--${tone}`,
              )}
              style={{
                left: `${taskLeftPercent}%`,
                width: `${taskWidthPercent}%`,
              }}
            />
          </div>
          {tone === 'blocked' ? (
            <span className="niuu-live-telemetry__breakdown-task-warning">
              blocked {formatCompactDurationMs(durationMs)}
            </span>
          ) : null}
        </div>
      </div>
      {node.children.map((child, index) => {
        const nextSibling = node.children[index + 1];
        return (
          <TelemetryTaskRow
            key={child.span.id}
            node={child}
            depth={depth + 1}
            stageStartedAtMs={stageStartedAtMs}
            stageDurationMs={stageDurationMs}
            nextSiblingStartedAtMs={nextSibling ? spanStartedAtMs(nextSibling.span) : null}
          />
        );
      })}
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
                  {stage.node.children.map((child, index) => {
                    const nextSibling = stage.node.children[index + 1];
                    return (
                      <TelemetryTaskRow
                        key={child.span.id}
                        node={child}
                        depth={1}
                        stageStartedAtMs={new Date(stage.row.startedAt).getTime()}
                        stageDurationMs={stage.row.durationMs}
                        nextSiblingStartedAtMs={
                          nextSibling ? spanStartedAtMs(nextSibling.span) : null
                        }
                      />
                    );
                  })}
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

export function TelemetryTab({
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
        badge: 'niuu:text-sky-300 niuu:bg-sky-500/12 niuu:border-sky-500/30',
        dot: 'niuu:bg-sky-400',
      };
    case 'file':
      return {
        badge: 'niuu:text-emerald-300 niuu:bg-emerald-500/12 niuu:border-emerald-500/30',
        dot: 'niuu:bg-emerald-400',
      };
    case 'git':
      return {
        badge: 'niuu:text-violet-300 niuu:bg-violet-500/12 niuu:border-violet-500/30',
        dot: 'niuu:bg-violet-400',
      };
    case 'terminal':
      return {
        badge: 'niuu:text-amber-300 niuu:bg-amber-500/12 niuu:border-amber-500/30',
        dot: 'niuu:bg-amber-400',
      };
    case 'error':
      return {
        badge: 'niuu:text-rose-300 niuu:bg-rose-500/12 niuu:border-rose-500/30',
        dot: 'niuu:bg-rose-400',
      };
    default:
      return {
        badge: 'niuu:text-text-secondary niuu:bg-bg-elevated niuu:border-border-subtle',
        dot: 'niuu:bg-text-muted',
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

/** Where the session works: its repository, or the folder it mounts. */
function sessionWorkspaceLabel(source: VolundrSession['source'] | null | undefined): string | null {
  if (!source) return null;
  if (source.type === 'git') return source.repo ? formatRepoLabel(source.repo) : null;
  return source.local_path ?? source.path ?? source.paths?.[0]?.host_path ?? null;
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
  if (!session?.source) return null;
  return (
    <SessionEnvironment
      session={session}
      repoUrl={normalizeRepoLink(session.source)}
      repoLabel={session.source.type === 'git' ? formatRepoLabel(session.source.repo) : undefined}
    />
  );
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
      ? 'niuu:border-rose-500/35 niuu:bg-rose-500/10 niuu:text-rose-200 niuu:hover:bg-rose-500/15'
      : tone === 'brand'
        ? 'niuu:border-brand/40 niuu:bg-brand/12 niuu:text-brand niuu:hover:bg-brand/18'
        : 'niuu:border-border-subtle niuu:bg-bg-elevated niuu:text-text-secondary niuu:hover:bg-bg-tertiary';

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'niuu:inline-flex niuu:appearance-none niuu:items-center niuu:justify-center niuu:rounded-md niuu:border niuu:px-3 niuu:py-1.5 niuu:font-mono niuu:text-[11px] niuu:transition-colors niuu:disabled:cursor-not-allowed niuu:disabled:opacity-45',
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

interface PrimaryStateAction {
  icon: typeof Play;
  title: string;
  label?: string;
  tone: 'critical' | 'brand';
  onSelect: () => void;
  /** Shown but not actionable, e.g. while the session boots. */
  disabled?: boolean;
}

/** The one lifecycle action that stays on the header: stop a live session, bring back a stopped or archived one. */
function sessionPrimaryAction({
  status,
  readOnly,
  onStop,
  onStart,
  onRestore,
}: {
  status: VolundrSession['status'] | undefined;
  readOnly: boolean;
  onStop: () => void;
  onStart: () => void;
  onRestore: () => void;
}): PrimaryStateAction | null {
  if (!status) return null;
  if (status === 'archived') {
    return {
      icon: RotateCcw,
      title: 'Restore archived session',
      label: 'Restore',
      tone: 'brand',
      onSelect: onRestore,
    };
  }
  if (readOnly) return null;
  if (status === 'running') {
    return {
      icon: Square,
      // A quiet icon, as Lexi keeps the header to the name and a few controls.
      title: 'Stop session',
      tone: 'critical',
      onSelect: onStop,
    };
  }
  if (isSessionBooting(status)) {
    // A booting session cannot be started again; say what it is doing instead.
    return {
      icon: Play,
      title: 'The session is starting',
      label: 'Starting…',
      tone: 'brand',
      onSelect: onStart,
      disabled: true,
    };
  }
  return { icon: Play, title: 'Start session', label: 'Start', tone: 'brand', onSelect: onStart };
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

/** The workflow run a session works for, opened on the run's review page. */
function RunLink({ runId }: { runId: string }) {
  const navigate = useNavigate();
  const to = `/ting/review/run/${encodeURIComponent(runId)}`;
  return (
    <span className="niuu-live-session__ticket" data-testid="session-run">
      <a
        href={to}
        title={`Open workflow run ${runId}`}
        onClick={(event) => {
          event.preventDefault();
          void navigate({ to: to as never });
        }}
      >
        {runId}
      </a>
    </span>
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
    <span className="niuu-live-session__ticket">
      <ConversationLink href={issue.url}>{issue.identifier}</ConversationLink>
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
                  className="niuu:sr-only"
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
                    <Check className="niuu:h-3 niuu:w-3" />
                  ) : null}
                </span>
                <div>
                  <div className="niuu:text-sm niuu:text-text-primary">
                    Delete workspace storage
                  </div>
                  <div className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
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
                  className="niuu:sr-only"
                />
                <span
                  aria-hidden="true"
                  className={cn(
                    'niuu-live-delete-dialog__checkbox',
                    cleanup.has('chronicles') && 'niuu-live-delete-dialog__checkbox--checked',
                  )}
                >
                  {cleanup.has('chronicles') ? <Check className="niuu:h-3 niuu:w-3" /> : null}
                </span>
                <div>
                  <div className="niuu:text-sm niuu:text-text-primary">Delete chronicles</div>
                  <div className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
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

export function LiveLogsTab({
  sessionId,
  volundr,
}: {
  sessionId: string;
  volundr: IVolundrService;
}) {
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
  const logs = logState.key === requestKey ? logState.logs : { lines: [], participants: [] };
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
    <div className="niuu:flex niuu:h-full niuu:flex-col" data-testid="live-logs-tab">
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
      <div className="niuu:flex niuu:h-full niuu:items-center niuu:justify-center niuu:text-sm niuu:text-text-muted">
        Loading chronicle…
      </div>
    );
  }

  if (!chronicle) {
    return (
      <div className="niuu:flex niuu:h-full niuu:items-center niuu:justify-center niuu:text-sm niuu:text-text-muted">
        {sessionStatus === 'running' ? 'No chronicle data yet.' : 'No saved chronicle yet.'}
      </div>
    );
  }

  const maxBurn = Math.max(1, ...chronicle.tokenBurn);
  const totalTokens = chronicle.events.reduce((sum, event) => sum + (event.tokens ?? 0), 0);

  return (
    <div className="niuu-live-chronicles-layout">
      <div className="niuu:flex niuu:min-h-0 niuu:flex-col niuu:border-r niuu:border-border-subtle niuu:bg-bg-primary">
        <div className="niuu:border-b niuu:border-border-subtle niuu:px-4 niuu:py-2.5">
          <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-4">
            <div className="niuu:min-w-0">
              <div className="niuu:whitespace-nowrap niuu:text-sm niuu:font-medium niuu:text-text-primary">
                Event timeline
              </div>
              <div className="niuu:mt-1 niuu:whitespace-nowrap niuu:font-mono niuu:text-[11px] niuu:text-text-muted">
                {chronicle.events.length} events · {formatCount(totalTokens)} tokens
              </div>
            </div>
            <div className="niuu:flex niuu:h-12 niuu:items-end niuu:gap-1">
              {chronicle.tokenBurn.map((value, index) => (
                <span
                  key={`${value}-${index}`}
                  className={cn(
                    'niuu:w-2 niuu:rounded-sm niuu:bg-brand/30',
                    value >= maxBurn * 0.75 && 'niuu:bg-brand/60',
                  )}
                  style={{ height: `${Math.max(14, (value / maxBurn) * 48)}px` }}
                  title={`${value} tokens`}
                />
              ))}
            </div>
          </div>
        </div>

        <div className="niuu:min-h-0 niuu:flex-1 niuu:overflow-auto niuu:px-4 niuu:py-3">
          <div className="niuu-live-chronicles-timeline">
            <div className="niuu-live-chronicles-rail" />
            <div className="niuu:flex niuu:flex-col">
              {chronicle.events.map((event, index) => {
                const tone = eventTone(event.type);
                const Icon = eventIcon(event.type);
                return (
                  <div
                    key={`${event.type}-${event.t}-${index}`}
                    className="niuu-live-chronicles-row niuu:border-b niuu:border-border-subtle/60 niuu:py-2.5 niuu:last:border-b-0"
                  >
                    <div className="niuu:pt-1 niuu:text-right niuu:font-mono niuu:text-[10px] niuu:text-text-faint">
                      {formatEventTime(event.t)}
                    </div>
                    <div className="niuu:relative niuu:flex niuu:justify-center">
                      <span
                        className={cn(
                          'niuu:relative niuu:z-[1] niuu:mt-1.5 niuu:inline-flex niuu:h-5 niuu:w-5 niuu:items-center niuu:justify-center niuu:rounded-md niuu:border niuu:bg-bg-secondary',
                          tone.badge,
                        )}
                      >
                        <Icon className="niuu:h-3 niuu:w-3" />
                      </span>
                    </div>
                    <div className="niuu:min-w-0">
                      <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-3">
                        <div className="niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-2">
                          <span className="niuu:font-mono niuu:text-[10px] niuu:uppercase niuu:tracking-[0.16em] niuu:text-text-faint">
                            {eventLabel(event.type)}
                          </span>
                          {event.action ? (
                            <span className="niuu:font-mono niuu:text-[10px] niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-muted">
                              {event.action}
                            </span>
                          ) : null}
                        </div>
                        <div className="niuu:flex niuu:flex-wrap niuu:justify-end niuu:gap-x-2 niuu:gap-y-1 niuu:font-mono niuu:text-[10px]">
                          {typeof event.tokens === 'number' && (
                            <span className="niuu:text-text-muted">
                              {formatCount(event.tokens)} tok
                            </span>
                          )}
                          {(typeof event.ins === 'number' || typeof event.del === 'number') && (
                            <span className="niuu:text-text-muted">
                              {typeof event.ins === 'number' ? `+${event.ins}` : ''}
                              {typeof event.del === 'number' ? ` / -${event.del}` : ''}
                            </span>
                          )}
                          {event.hash && <span className="niuu:text-text-faint">{event.hash}</span>}
                          {typeof event.exit === 'number' && (
                            <span
                              className={
                                event.exit === 0 ? 'niuu:text-emerald-300' : 'niuu:text-rose-300'
                              }
                            >
                              exit {event.exit}
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="niuu:mt-1 niuu:text-[13px] niuu:leading-5 niuu:text-text-primary">
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

      <div className="niuu:flex niuu:min-h-0 niuu:flex-col niuu:overflow-auto niuu:bg-bg-secondary niuu:p-3">
        {session?.trackerIssue && (
          <section className="niuu:mb-3 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:p-3">
            <div className="niuu:mb-2 niuu:font-mono niuu:text-[10px] niuu:uppercase niuu:tracking-[0.18em] niuu:text-text-faint">
              Tracker
            </div>
            <a
              href={session.trackerIssue.url}
              target="_blank"
              rel="noreferrer"
              className="niuu:inline-flex niuu:items-center niuu:gap-2 niuu:text-sm niuu:text-brand niuu:hover:underline"
            >
              <span className="niuu:rounded-md niuu:border niuu:border-brand/35 niuu:bg-brand/10 niuu:px-2 niuu:py-0.5 niuu:font-mono niuu:text-[11px]">
                {session.trackerIssue.identifier}
              </span>
              <span className="niuu:text-text-primary">{session.trackerIssue.title}</span>
            </a>
          </section>
        )}

        <section className="niuu:mb-3 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:p-3">
          <div className="niuu:mb-2.5 niuu:flex niuu:items-center niuu:justify-between">
            <div className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
              Files modified
            </div>
            <div className="niuu:font-mono niuu:text-[11px] niuu:text-text-muted">
              {chronicle.files.length}
            </div>
          </div>
          <div className="niuu:flex niuu:flex-col">
            {chronicle.files.length > 0 ? (
              chronicle.files.map((file, index) => (
                <button
                  key={file.path}
                  type="button"
                  onClick={() => handleCopyPath(file.path)}
                  className={cn(
                    'niuu:flex niuu:w-full niuu:items-start niuu:gap-2 niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-2.5 niuu:py-2 niuu:text-left niuu:hover:bg-bg-tertiary',
                    index > 0 && 'niuu:border-t',
                  )}
                  title={copiedPath === file.path ? 'Copied' : `${file.path} · click to copy`}
                >
                  <span className="niuu:mt-0.5 niuu:inline-flex niuu:h-5 niuu:w-5 niuu:flex-shrink-0 niuu:items-center niuu:justify-center niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary">
                    {copiedPath === file.path ? (
                      <Check className="niuu:h-3 niuu:w-3 niuu:text-brand" />
                    ) : (
                      <FilePenLine className="niuu:h-3 niuu:w-3 niuu:text-text-muted" />
                    )}
                  </span>
                  <div className="niuu:min-w-0 niuu:flex-1">
                    <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-3">
                      <span className="niuu:truncate niuu:font-mono niuu:text-[11px] niuu:text-text-primary">
                        {truncateLeadingPath(file.path)}
                      </span>
                      <span className="niuu:flex-shrink-0 niuu:font-mono niuu:text-[10px] niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-faint">
                        {file.status}
                      </span>
                    </div>
                    <div className="niuu:mt-0.5 niuu:font-mono niuu:text-[10px] niuu:text-text-muted">
                      +{file.ins} / -{file.del}
                    </div>
                  </div>
                </button>
              ))
            ) : (
              <div className="niuu:text-sm niuu:text-text-muted">No file changes yet.</div>
            )}
          </div>
        </section>

        <section className="niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:p-3">
          <div className="niuu:mb-2.5 niuu:flex niuu:items-center niuu:justify-between">
            <div className="niuu:text-sm niuu:font-medium niuu:text-text-primary">Commits</div>
            <div className="niuu:font-mono niuu:text-[11px] niuu:text-text-muted">
              {chronicle.commits.length}
            </div>
          </div>
          <div className="niuu:flex niuu:flex-col">
            {chronicle.commits.length > 0 ? (
              chronicle.commits.map((commit, index) => (
                <div
                  key={commit.hash}
                  className={cn(
                    'niuu:flex niuu:items-start niuu:gap-2 niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-2.5 niuu:py-2',
                    index > 0 && 'niuu:border-t',
                  )}
                >
                  <span className="niuu:mt-0.5 niuu:inline-flex niuu:h-5 niuu:w-5 niuu:flex-shrink-0 niuu:items-center niuu:justify-center niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary">
                    <GitCommitHorizontal className="niuu:h-3 niuu:w-3 niuu:text-text-muted" />
                  </span>
                  <div className="niuu:min-w-0 niuu:flex-1">
                    <div className="niuu:truncate niuu:text-[12px] niuu:text-text-primary">
                      {commit.msg}
                    </div>
                    <div className="niuu:mt-0.5 niuu:flex niuu:items-center niuu:gap-2 niuu:font-mono niuu:text-[10px] niuu:text-text-muted">
                      <span>{commit.hash.slice(0, 8)}</span>
                      <span className="niuu:text-text-faint">•</span>
                      <span>{commit.time}</span>
                    </div>
                  </div>
                </div>
              ))
            ) : (
              <div className="niuu:text-sm niuu:text-text-muted">No commits yet.</div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

function authHeaders(): Record<string, string> {
  return Object.fromEntries(getAuthHeaders().entries());
}

interface SessionFileListPayload {
  entries?: Array<FileEntry & { size?: number; modified?: string }>;
}

async function fetchSessionFiles(apiBase: string, path: string): Promise<FileEntry[]> {
  const base = apiBase.endsWith('/') ? apiBase : `${apiBase}/`;
  const url = new URL('api/files', base);
  url.searchParams.set('root', 'workspace');
  if (path) url.searchParams.set('path', path);

  const response = await fetch(url.toString(), { headers: authHeaders() });
  if (!response.ok) return [];
  const payload = (await response.json()) as SessionFileListPayload;
  return (payload.entries ?? [])
    .filter((entry) => entry.type === 'file' || entry.type === 'directory')
    .map((entry) => ({
      name: entry.name,
      path: entry.path,
      type: entry.type,
    }));
}

export async function fetchSessionMentionFiles(
  query: string,
  apiBase: string,
): Promise<FileEntry[]> {
  const normalized = query.trim().replace(/^\/+/, '');
  if (!normalized) return fetchSessionFiles(apiBase, '');

  if (normalized.endsWith('/')) {
    return fetchSessionFiles(apiBase, normalized.replace(/\/+$/, ''));
  }

  const slashIndex = normalized.lastIndexOf('/');
  const parentPath = slashIndex >= 0 ? normalized.slice(0, slashIndex) : '';
  const term = (slashIndex >= 0 ? normalized.slice(slashIndex + 1) : normalized).toLowerCase();
  const entries = await fetchSessionFiles(apiBase, parentPath);
  if (!term) return entries;
  return entries.filter(
    (entry) => entry.name.toLowerCase().includes(term) || entry.path.toLowerCase().includes(term),
  );
}

/**
 * A session list that shows a room's workers under the session selects them: it says which
 * worker the chat shows on its own, and hears who the workers are.
 */
export interface RoomWorkerFocus {
  focusedWorkerId?: string | null;
  onFocusWorker?: (peerId: string | null) => void;
  onRoomWorkers?: (workers: readonly Worker[]) => void;
  /** Opened from the unread list: the header leads back to it, as Lexi's "‹ Unread". */
  onBackToUnread?: () => void;
  /** The host lists the workers under the coordinator, so the chat does not repeat them. */
  workersInList?: boolean;
  /** Told when the right dock opens or closes, so the host can make room for it. */
  onDockOpenChange?: (open: boolean) => void;
}

export function LiveSessionDetailPage({
  sessionId,
  instanceId,
  returnTo,
  readOnly = false,
  initialTab = 'chat',
  focusedWorkerId,
  onFocusWorker,
  onRoomWorkers,
  onBackToUnread,
  workersInList = false,
  onDockOpenChange,
}: {
  sessionId: string;
  instanceId?: string;
  returnTo?: string;
  readOnly?: boolean;
  initialTab?: SessionTab;
} & RoomWorkerFocus) {
  return (
    <LiveSessionDetailPageInner
      key={`${instanceId ?? ''}:${sessionId}`}
      sessionId={sessionId}
      instanceId={instanceId}
      returnTo={normalizeWorkReturnTo(returnTo)}
      readOnly={readOnly}
      initialTab={initialTab}
      focusedWorkerId={focusedWorkerId}
      onFocusWorker={onFocusWorker}
      onRoomWorkers={onRoomWorkers}
      onBackToUnread={onBackToUnread}
      workersInList={workersInList}
      onDockOpenChange={onDockOpenChange}
    />
  );
}

function LiveSessionDetailPageInner({
  sessionId,
  instanceId,
  returnTo,
  readOnly = false,
  initialTab = 'chat',
  focusedWorkerId,
  onFocusWorker,
  onRoomWorkers,
  onBackToUnread,
  workersInList = false,
  onDockOpenChange,
}: {
  sessionId: string;
  instanceId?: string;
  returnTo: string | null;
  readOnly?: boolean;
  initialTab?: SessionTab;
} & RoomWorkerFocus) {
  const [activeTab, setActiveTab] = useState<SessionTab>(initialTab);
  // A worker picked in the list is shown in the chat, whichever tab was open.
  const [shownFocus, setShownFocus] = useState(focusedWorkerId);
  if (focusedWorkerId !== shownFocus) {
    setShownFocus(focusedWorkerId);
    if (focusedWorkerId) setActiveTab('chat');
  }
  const [tabWasManuallySelected, setTabWasManuallySelected] = useState(false);
  const [actionBusy, setActionBusy] = useState<
    'start' | 'stop' | 'archive' | 'restore' | 'delete' | null
  >(null);
  // Why the last toolbar action was refused (a restart with no free slot, say).
  const [actionError, setActionError] = useState<string | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [forwarding, setForwarding] = useState<ChatMessage | null>(null);
  const [forwardedNotice, setForwardedNotice] = useState<ForwardedNotice | null>(null);
  const [dismissedHumanGateIds, setDismissedHumanGateIds] = useState<Set<string>>(new Set());
  const [toolVisibility, setToolVisibility] = useForgePreference('tools', '1', ['0', '1']);
  const minuteNow = useMinuteClock();
  // Changes, Files, a previewed file and a side chat open beside the conversation, as tabs of
  // the right dock, as Lexi's do.
  const [dock, dispatchDock] = useReducer(dockReducer, EMPTY_DOCK);
  const shownPane = activeDockPane(dock);
  const openPane = useCallback((pane: DockPane) => dispatchDock({ type: 'open', pane }), []);
  const [quickOpen, setQuickOpen] = useState(false);
  // "Open in Files" from a previewed file shows that file in the tree's pane.
  const [filesFocus, setFilesFocus] = useState<string | undefined>(undefined);
  const { width: dockPreferredWidth, resizer: dockResizer } = useResizableSidebar(
    'niuu.forge.dockWidth',
    DOCK_WIDTH,
  );
  const [sessionRow, setSessionRow] = useState<HTMLDivElement | null>(null);
  const sessionRowWidth = useElementWidth(sessionRow);
  // As Lexi's resize contract: the dock narrows to keep the conversation readable and steps
  // aside, still chosen, when even its narrowest would squeeze it; it returns when wider.
  const dockWidth =
    sessionRowWidth === null
      ? dockPreferredWidth
      : Math.min(dockPreferredWidth, sessionRowWidth - TRANSCRIPT_MIN_WIDTH);
  const dockOpen = shownPane !== null;
  const dockShown = dockOpen && dockWidth >= DOCK_WIDTH.min;
  // The terminal docks under the conversation and the right dock. The choice to show it is
  // kept; it shortens to keep the conversation usable and hides for the moment when even its
  // lowest would squeeze it, as the right dock steps aside.
  const [terminalPreference, setTerminalPreference] = useForgePreference('terminal', '0', [
    '0',
    '1',
  ]);
  const terminalOpen = terminalPreference === '1';
  const { width: terminalPreferredHeight, resizer: terminalResizer } = useResizableSidebar(
    'niuu.forge.terminalHeight',
    TERMINAL_HEIGHT,
  );
  const [workArea, setWorkArea] = useState<HTMLDivElement | null>(null);
  const workAreaHeight = useElementHeight(workArea);
  const terminalHeight =
    workAreaHeight === null
      ? terminalPreferredHeight
      : Math.min(terminalPreferredHeight, workAreaHeight - CONVERSATION_MIN_HEIGHT);
  const terminalShown = terminalOpen && terminalHeight >= TERMINAL_HEIGHT.min;
  useEffect(() => {
    if (!onDockOpenChange) return;
    onDockOpenChange(dockOpen);
    return () => onDockOpenChange(false);
  }, [dockOpen, onDockOpenChange]);
  const showInternalMessages = toolVisibility === '1';
  const [tokenVisibility] = useForgePreference('tokens', '0', ['0', '1']);
  const showTokenUsage = tokenVisibility === '1';
  const [detailsPreference] = useForgePreference('details', '0', ['0', '1']);
  const showDetails = detailsPreference === '1';
  // The panel stays out of the way, as Lexi's dock does ('auto' is closed); once the person
  // opens or closes it, that choice holds.
  const [panelVisibility, setPanelVisibility] = useForgePreference('sidePanel', 'auto', [
    'auto',
    'open',
    'closed',
  ]);
  // Below this width the panel has no room for a column (SessionAttentionPanel.css), so it
  // starts closed and opens over the chat; the saved preference is for wide screens.
  const narrowScreen = useMediaQuery(PANEL_OVERLAY_QUERY);
  const [overlayPanelOpen, setOverlayPanelOpen] = useState(false);
  const setPanelOpen = (open: boolean) =>
    narrowScreen ? setOverlayPanelOpen(open) : setPanelVisibility(open ? 'open' : 'closed');
  const review = useReviewComments();
  // Signing an AI or Git account in again without leaving the session.
  const openConnections = useContext(ChatConnectionsContext);
  const [visibleMessageCount, setVisibleMessageCount] = useState<number | null>(null);
  const volundr = useService<IVolundrService>('volundr');
  const filesystem = useService<IFileSystemPort>('filesystem');
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const sessionQuery = useSessionDetail(sessionId, instanceId);
  const sessionListQuery = useSessionList();
  const sessionRead = useSessionRead();
  const liveSessionQuery = useQuery({
    queryKey: ['volundr', 'raw-session', sessionId, instanceId ?? null],
    queryFn: ({ signal }) => volundr.getSession(sessionId, { instanceId, signal }),
    // A stopped, archived or failed session changes only through an action, which
    // invalidates this query, or a pushed state change (below).
    refetchInterval: (query) =>
      query.state.data && SETTLED_RAW_STATUSES.has(query.state.data.status) ? false : 5_000,
  });
  const domainState = sessionQuery.data?.state;
  useEffect(() => {
    if (!domainState) return;
    void queryClient.invalidateQueries({
      queryKey: ['volundr', 'raw-session', sessionId, instanceId ?? null],
    });
  }, [domainState, instanceId, queryClient, sessionId]);
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
  const historyLocator = useOptionalService<ISessionHistoryLocator>('forge.history');
  const historyEndpoint = useMemo(
    () => historyLocator?.historyEndpoint(chatEndpoint) ?? null,
    [chatEndpoint, historyLocator],
  );
  const chat = useSkuldChat(chatEndpoint, { historyEndpoint });
  // Each Forge host has its own preview domain: ask the one that runs this session.
  const previewInstanceId = liveSession?.instanceId ?? instanceId;
  const livePreviewQuery = useQuery({
    queryKey: ['volundr', 'live-preview-config', previewInstanceId ?? null],
    queryFn: () => volundr.getLivePreviewConfig(previewInstanceId),
    enabled: Boolean(liveSession),
    staleTime: Infinity,
  });
  const livePreview = livePreviewQuery.data ?? LIVE_PREVIEW_DISABLED;
  // A session that failed before its first message never had a broker, so it
  // has no transcript to replay; asking for one only earns a misleading error.
  const failedBeforeStart =
    (sessionStatus === 'failed' || sessionStatus === 'error') && liveSession?.messageCount === 0;
  const transcriptQuery = useQuery({
    queryKey: ['volundr', 'conversation-history', sessionId, instanceId ?? null],
    queryFn: () => volundr.getConversationHistory(sessionId),
    enabled: Boolean(sessionId) && canReplayTranscript && !failedBeforeStart,
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
  const transcriptTurns = useMemo(() => transcriptQuery.data?.turns ?? [], [transcriptQuery.data]);
  const replayMessages = useMemo(() => transformTurns(transcriptTurns), [transcriptTurns]);
  const presentedFiles = useMemo(
    () => presentedFilesIn([...replayMessages, ...chat.messages]),
    [replayMessages, chat.messages],
  );
  const replayParticipants = useMemo(
    () => participantsFromTurns(transcriptTurns),
    [transcriptTurns],
  );
  const replayMeshEvents = useMemo(() => meshEventsFromTurns(transcriptTurns), [transcriptTurns]);
  const activeHumanGate = useMemo(() => {
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
      eventId,
      event: {
        summary: latest.summary || 'Workflow gate is waiting for human approval.',
        reason: latest.reason,
        recommendation: latest.recommendation,
        persona: latest.persona,
        timestamp: latest.timestamp,
      },
      participant: {
        peerId: participant.peerId,
        displayName: participant.displayName,
        persona: participant.persona,
        participantType: participant.participantType,
      },
    };
  }, [chat.meshEvents, chat.participants, dismissedHumanGateIds]);
  const attentionItems = useMemo(() => {
    const gates: SessionGateSummary[] = [];
    if (activeHumanGate) {
      gates.push({
        id: activeHumanGate.eventId,
        title: activeHumanGate.participant.displayName || 'Workflow gate',
        detail: activeHumanGate.event.summary,
      });
    }
    return buildAttentionItems({
      gates,
      permissions: chat.pendingPermissions,
      inputRequests: chat.pendingInputRequests,
    });
  }, [activeHumanGate, chat.pendingInputRequests, chat.pendingPermissions]);
  const checklist = useMemo(
    () => resolveChecklist(chat.plan, chat.messages),
    [chat.plan, chat.messages],
  );
  const sessionDetail = sessionQuery.data;
  const costValue =
    sessionDetail?.costCents != null ? formatCurrencyCents(sessionDetail.costCents) : null;
  // The pill, elapsed time and composer read the same pushed session, so they agree.
  const sessionWorking = sessionDetail
    ? sessionDetail.state === 'running'
    : isSessionWorking(liveSession?.activityState);
  const contextMeter = contextMeterData(chat.usage);
  const sessionProjectId = liveSession?.coordination?.projectId;
  // Who leads this session and whom it leads, read from the session list the page lists by.
  const knownSessions = sessionListQuery.data;
  const coordination = useMemo(
    () => coordinationOf(knownSessions ?? [], sessionId),
    [knownSessions, sessionId],
  );
  const isCoordinator =
    liveSession?.coordination?.role === 'coordinator' || coordination.workers.length > 0;
  // A workflow run's first session hosts the run's room: its members are in it from Forge's
  // record, whether the room is live or the run has finished.
  const runHost = useMemo(
    () => (liveSession ? { id: sessionId, runId: liveSession.runId } : null),
    [liveSession, sessionId],
  );
  const roomParticipants = useMemo(
    () => withRunMembers(chat.participants, runHost, coordination.workers),
    [chat.participants, runHost, coordination.workers],
  );
  const replayRoomParticipants = useMemo(
    () => withRunMembers(replayParticipants, runHost, coordination.workers),
    [replayParticipants, runHost, coordination.workers],
  );
  const hasCoordinator = Boolean(liveSession?.coordination?.parent);
  const openSession = useCallback(
    (id: string) =>
      void navigate({ to: '/volundr/sessions/$sessionId', params: { sessionId: id } }),
    [navigate],
  );
  const sessionLinks = useMemo(
    () => buildSessionLinks(knownSessions ?? [], sessionId, openSession),
    [knownSessions, openSession, sessionId],
  );
  // Secret requests answered in this session's chat: the value goes to Forge with the
  // person's own credentials, bound to this session, and is never kept here.
  const secretRequests = useMemo<SecretRequestsPort>(
    () => ({
      get: (requestId) => volundr.getSecretRequest(sessionId, requestId),
      fulfill: (requestId, fulfillment) =>
        volundr.fulfillSecretRequest(sessionId, requestId, fulfillment),
      decline: (requestId) => volundr.declineSecretRequest(sessionId, requestId),
    }),
    [sessionId, volundr],
  );
  const workerSessionIds = useMemo(
    () => coordination.workers.map((worker) => worker.id),
    [coordination.workers],
  );
  const projectsQuery = useQuery({
    queryKey: ['volundr', 'projects', 'session-detail', liveSession?.instanceId ?? null],
    queryFn: ({ signal }) => volundr.getProjects({ instanceId: liveSession?.instanceId, signal }),
    enabled: Boolean(sessionProjectId),
    retry: false,
  });
  const sessionProjectName = projectsQuery.data?.find(
    (project) => project.id === sessionProjectId,
  )?.name;
  const sessionModel = liveSession?.model;
  const sessionSessionDefinition = liveSession?.sessionDefinition;
  const sessionOrigin = liveSession?.origin;
  const sessionEngineHarness = useMemo(
    () =>
      sessionModel
        ? sessionHarness({
            model: sessionModel,
            sessionDefinition: sessionSessionDefinition,
            origin: sessionOrigin,
          })
        : undefined,
    [sessionModel, sessionOrigin, sessionSessionDefinition],
  );
  const sessionSource = liveSession?.source;
  const infoRows = useMemo(
    () =>
      sessionInfoRows({
        engine: sessionModel
          ? { model: modelLabel(sessionModel), harness: sessionEngineHarness }
          : undefined,
        host: forgeBadgeLabel,
        workspace: sessionWorkspaceLabel(sessionSource),
        project: sessionProjectName,
        objective: liveSession?.coordination?.objective,
        startedAt: sessionDetail?.startedAt,
      }),
    [
      liveSession?.coordination?.objective,
      forgeBadgeLabel,
      sessionDetail?.startedAt,
      sessionEngineHarness,
      sessionModel,
      sessionProjectName,
      sessionSource,
    ],
  );
  const tabCounts: Partial<Record<SessionTab, number>> = {
    chat: liveSession?.messageCount || replayMessages.length || undefined,
    diffs: fileChangeCount(sessionDetail?.files),
    chronicles: sessionDetail?.events.length ? sessionDetail.events.length : undefined,
  };
  const ravnWorkers = useMemo(
    () =>
      isReady
        ? roomWorkers(roomParticipants, chat.meshEvents, chat.messages, sessionId)
        : roomWorkers(replayRoomParticipants, replayMeshEvents, replayMessages, sessionId),
    [
      isReady,
      roomParticipants,
      chat.meshEvents,
      chat.messages,
      replayRoomParticipants,
      replayMeshEvents,
      replayMessages,
      sessionId,
    ],
  );
  const panelHasContent = sessionPanelHasContent({
    workerSessions: coordination.workers,
    ravnWorkers,
    items: attentionItems,
    ticket: sessionTrackerIssue,
    checklist,
    changedFiles: tabCounts.diffs,
    agents: chat.agents,
    deliverables: presentedFiles,
    info: infoRows,
  });
  // The panel opens when the person opens it. What needs them sits by the composer (the
  // decision strip and the questions), and the panel's button says "Needs you N" meanwhile.
  const showPanel = narrowScreen ? overlayPanelOpen : panelVisibility === 'open';
  const headerMessageCount = useMemo(() => {
    if (visibleMessageCount != null) return visibleMessageCount;
    if (canReplayTranscript) return replayMessages.length;
    return liveSession?.messageCount ?? 0;
  }, [canReplayTranscript, liveSession?.messageCount, replayMessages.length, visibleMessageCount]);
  const isSessionConnected = isReady && chat.connected;

  const { enabled: enabledTabs } = useSessionTabs();
  const tabs = ALL_TABS.filter((tab) => enabledTabs.has(tab.id));

  const handleToggleInternalMessages = useCallback(() => {
    const next = !showInternalMessages;
    setToolVisibility(next ? '1' : '0');
  }, [showInternalMessages, setToolVisibility]);

  const isFlockSession = useMemo(
    () =>
      Array.from(chat.participants.values()).some(
        (participant) => participant.participantType === 'ravn',
      ),
    [chat.participants],
  );
  const canShowResendPromptToFlock =
    isFlockSession && chat.capabilities.room_prompt_resend === true;

  const canResendPromptToFlock =
    isReady &&
    isSessionConnected &&
    !readOnly &&
    liveSession?.status === 'running' &&
    canShowResendPromptToFlock;

  const handleHumanGateReply = useCallback(
    (decision: 'APPROVE' | 'CHANGES_REQUESTED', notes: string) => {
      if (!activeHumanGate) return;
      setDismissedHumanGateIds((current) => {
        const next = new Set(current);
        next.add(activeHumanGate.eventId);
        return next;
      });
      void chat.sendDirectedMessages(
        [activeHumanGate.participant],
        notes ? `${decision}\n\n${notes}` : decision,
        [],
      );
    },
    [activeHumanGate, chat],
  );

  const resolvedActiveTab =
    !tabWasManuallySelected && tabs.some((tab) => tab.id === 'chat')
      ? 'chat'
      : tabs.some((tab) => tab.id === activeTab)
        ? activeTab
        : (tabs.find((tab) => tab.id === 'chat')?.id ?? tabs[0]?.id ?? 'chat');

  const shownKind = shownPane?.kind ?? null;
  const openTab = useCallback(
    (tab: SessionTab) => {
      if (tab === 'diffs' || tab === 'files') {
        const kind = tab === 'diffs' ? 'changes' : 'files';
        if (shownKind === kind) dispatchDock({ type: 'hide' });
        else openPane({ kind });
        return;
      }
      setTabWasManuallySelected(true);
      setActiveTab(tab);
    },
    [openPane, shownKind],
  );
  const panelShown = showPanel && !dockOpen;
  const tabSelected = (tab: SessionTab) =>
    tab === 'diffs'
      ? shownKind === 'changes'
      : tab === 'files'
        ? shownKind === 'files'
        : resolvedActiveTab === tab;
  const toggleTerminal = useCallback(
    () => setTerminalPreference(terminalOpen ? '0' : '1'),
    [setTerminalPreference, terminalOpen],
  );

  // As Lexi's: ⌥⌘O quick opens a file into the dock, ⌥⌘→ shows or hides the dock and ⌃`
  // the terminal (Ctrl+Alt stands in for ⌥⌘ off a Mac).
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.ctrlKey && !event.shiftKey && !event.altKey && event.code === 'Backquote') {
        event.preventDefault();
        toggleTerminal();
        return;
      }
      if (!event.altKey || !(event.metaKey || event.ctrlKey)) return;
      if (event.code === 'KeyO') {
        event.preventDefault();
        setQuickOpen(true);
        return;
      }
      if (event.code === 'ArrowRight') {
        event.preventDefault();
        dispatchDock({ type: 'toggle' });
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [toggleTerminal]);

  // The files the agent read or wrote, newest first: marked in the tree, first in quick open.
  const touchedPaths = useMemo(() => {
    const paths: string[] = [];
    for (const message of [...chat.messages].reverse()) {
      for (const part of message.parts ?? []) {
        if (part.type !== 'tool_use' || !part.input) continue;
        const path = part.input.file_path ?? part.input.path ?? part.input.notebook_path;
        if (typeof path === 'string' && path && !paths.includes(path)) paths.push(path);
      }
    }
    return paths;
  }, [chat.messages]);
  // Quick open searches the files the agent touched, the files the session changed, then the
  // workspace's first two levels (what the files API lists without opening a folder).
  const quickOpenChangesQuery = useQuery({
    queryKey: ['volundr', 'quick-open-changes', sessionId, chatEndpoint],
    queryFn: ({ signal }) => fetchSessionDiffFiles(chatEndpoint!, 'last-commit', signal),
    enabled: quickOpen && isReady && Boolean(chatEndpoint),
  });
  const quickOpenTreeQuery = useQuery({
    queryKey: ['volundr', 'filetree', sessionId, 'quick-open'],
    queryFn: () => filesystem.listTree(sessionId),
    enabled: quickOpen && isRunning,
  });
  const quickOpenCandidates = useMemo(() => {
    const listed: string[] = [];
    const walk = (nodes: readonly FileTreeNode[]) => {
      for (const node of nodes) {
        if (node.kind === 'file') listed.push(node.path);
        if (node.children) walk(node.children);
      }
    };
    walk(quickOpenTreeQuery.data ?? []);
    const changed = (quickOpenChangesQuery.data ?? [])
      .filter((file) => file.status !== 'del')
      .flatMap((file) => resolveSessionResource(file.path)?.path ?? []);
    return [...touchedPaths, ...changed, ...listed];
  }, [quickOpenChangesQuery.data, quickOpenTreeQuery.data, touchedPaths]);
  const openFilePane = useCallback((path: string) => openPane({ kind: 'file', path }), [openPane]);
  // Live preview: the ports the session listens on, from its Skuld's preview_ports frames.
  const previewPorts = useMemo(
    () => (livePreview.enabled && isRunning ? (chat.previewPorts ?? []) : []),
    [chat.previewPorts, isRunning, livePreview.enabled],
  );
  const openPreviewPane = useCallback(
    (port: number | null, path = '/') => openPane({ kind: 'preview', port, path }),
    [openPane],
  );
  // A loopback URL in the transcript (http://localhost:5173/docs) opens in the live preview.
  const openLoopbackLink = useCallback(
    (href: string) => {
      if (!livePreview.enabled || !isRunning) return false;
      const match = findLocalUrls(href).find((found) => found.text.length >= href.length - 1);
      if (!match) return false;
      openPreviewPane(match.port, match.path);
      return true;
    },
    [isRunning, livePreview.enabled, openPreviewPane],
  );

  // "New chat" in the side chat opens the launch flow in this session's repository and branch.
  const sideNewChat = useMemo(() => {
    const source = liveSession?.source;
    if (source?.type !== 'git' || !source.repo) return undefined;
    return () =>
      void navigate({
        to: '/volundr/sessions/new',
        search: {
          repo: source.repo,
          branch: source.branch,
          back: `/volundr/sessions/${sessionId}`,
        } as never,
      });
  }, [liveSession?.source, navigate, sessionId]);

  const dockTabs: RightDockTab[] = dock.panes.map((pane) => {
    const id = dockPaneId(pane);
    switch (pane.kind) {
      case 'changes':
        return { id, label: 'Changes', icon: FileDiff };
      case 'files':
        return { id, label: 'Files', icon: FolderOpen };
      case 'file':
        return {
          id,
          label: pane.path.split('/').at(-1) ?? pane.path,
          icon: FileText,
          title: pane.path,
        };
      case 'side':
        return { id, label: sideChatLabel(sessionName), icon: CornerDownRight };
      case 'preview': {
        const port = pane.port ?? previewPorts[0];
        return { id, label: port ? `Preview :${port}` : 'Live preview', icon: Globe };
      }
    }
  });
  const dockAddItems: MoreMenuItem[] = [
    {
      id: 'changes',
      label: 'Changes',
      icon: FileDiff,
      onSelect: () => openPane({ kind: 'changes' }),
    },
    {
      id: 'file',
      label: 'Preview file…',
      icon: FileText,
      shortcut: '⌥⌘O',
      disabled: !isRunning,
      onSelect: () => setQuickOpen(true),
    },
    {
      id: 'side',
      label: 'Side chat',
      icon: CornerDownRight,
      disabled: !liveSession,
      onSelect: () => openPane({ kind: 'side', childId: null }),
    },
    {
      id: 'preview',
      label: 'Live preview',
      icon: Globe,
      disabled: !isRunning || !livePreview.enabled,
      onSelect: () => openPreviewPane(previewPorts[0] ?? null),
    },
    {
      id: 'files',
      label: 'Files',
      icon: FolderOpen,
      separated: true,
      onSelect: () => openPane({ kind: 'files' }),
    },
  ];

  const handleSendReview = useCallback(() => {
    chat.sendMessage(formatReviewMessage(review.comments), []);
    review.clear();
    openTab('chat');
  }, [chat, openTab, review]);

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
    setActionError(null);
    try {
      await volundr.resumeSession(liveSession.id);
      await refreshSessionData();
    } catch (error) {
      // The platform's answer says what stopped the start and where to fix it.
      setActionError(errorText(error, 'The session could not be started'));
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
      ]);
      await volundr.deleteSession(liveSession.id, cleanup);
      setDeleteDialogOpen(false);
      queryClient.removeQueries({ queryKey: ['volundr', 'raw-session', sessionId] });
      queryClient.removeQueries({ queryKey: ['volundr', 'raw-session', 'logs', sessionId] });
      queryClient.removeQueries({ queryKey: ['volundr', 'conversation-history', sessionId] });
      queryClient.removeQueries({ queryKey: ['volundr', 'domain-session', sessionId] });
      queryClient.removeQueries({ queryKey: ['volundr', 'filetree', sessionId] });
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

  const primaryStateAction = sessionPrimaryAction({
    status: liveSession?.status,
    readOnly: Boolean(readOnly),
    onStop: () => void handleStopSession(),
    onStart: () => void handleResumeSession(),
    onRestore: () => void handleRestoreSession(),
  });
  const moreMenuItems: MoreMenuItem[] = [];
  const readTarget = sessionQuery.data;
  if (readTarget?.readState) {
    const unread = readTarget.readState.isUnread;
    moreMenuItems.push({
      id: 'read-state',
      label: unread ? 'Mark as read' : 'Mark as unread',
      icon: unread ? MailOpen : Mail,
      onSelect: () =>
        void (unread ? sessionRead.markRead(readTarget) : sessionRead.markUnread(readTarget)),
      disabled: sessionRead.pendingIds.has(readTarget.id),
    });
  }
  if (!readOnly && openConnections) {
    moreMenuItems.push({
      id: 'reconnect',
      label: 'Reconnect account',
      icon: KeyRound,
      onSelect: openConnections,
    });
  }
  if (canShowResendPromptToFlock) {
    moreMenuItems.push({
      id: 'resend',
      label: 'Resend prompt to flock',
      icon: MessageCircleReply,
      onSelect: chat.sendResendPrompt,
      disabled: !canResendPromptToFlock,
    });
  }
  if (!readOnly && liveSession && liveSession.status !== 'archived') {
    moreMenuItems.push({
      id: 'archive',
      label: 'Archive session',
      icon: Archive,
      onSelect: () => void handleArchiveSession(),
      disabled: actionBusy !== null,
    });
  }
  if (!readOnly) {
    moreMenuItems.push({
      id: 'delete',
      label: 'Delete session',
      icon: Trash2,
      onSelect: () => setDeleteDialogOpen(true),
      disabled: actionBusy !== null,
      tone: 'critical',
      separated: moreMenuItems.length > 0,
    });
  }

  if (sessionQuery.isLoading || liveSessionQuery.isLoading) {
    return <LoadingState className="forge-session-loading" label="Loading session…" />;
  }

  if (sessionQuery.isError || liveSessionQuery.isError) {
    const error = sessionQuery.error ?? liveSessionQuery.error;
    return (
      <ErrorState
        title="Failed to load session"
        message={error instanceof Error ? error.message : 'Unknown error'}
      />
    );
  }

  // A waiting decision sits by the composer, folded to a strip, as Lexi keeps its questions.
  const gateRequestedAt = activeHumanGate ? activeHumanGate.event.timestamp.getTime() : Number.NaN;
  const gateWaitingLabel = Number.isFinite(gateRequestedAt)
    ? compactDuration(Math.max(0, minuteNow - gateRequestedAt))
    : undefined;
  const decisionDock = activeHumanGate ? (
    <DecisionDock
      title={activeHumanGate.event.summary || 'Workflow gate'}
      waiting={gateWaitingLabel}
    >
      <WorkflowHumanGateCard
        title={
          activeHumanGate.participant.displayName ||
          activeHumanGate.participant.persona ||
          activeHumanGate.event.persona ||
          'Workflow gate'
        }
        summary={activeHumanGate.event.summary || 'Workflow gate'}
        reason={activeHumanGate.event.reason}
        recommendation={activeHumanGate.event.recommendation}
        comments={review.comments}
        onApprove={(notes) => {
          handleHumanGateReply('APPROVE', notes);
          review.clear();
        }}
        onRequestChanges={(notes) => {
          handleHumanGateReply('CHANGES_REQUESTED', notes);
          review.clear();
        }}
      />
    </DecisionDock>
  ) : null;

  return (
    <SessionResources
      key={sessionId}
      sessionId={sessionId}
      workspace={
        liveSession?.source.type === 'local_mount'
          ? (liveSession.source.local_path ?? liveSession.source.path)
          : undefined
      }
      filesystem={filesystem}
      presented={presentedFiles}
      filesReadable={isRunning}
      onOpenWorkspaceFile={openFilePane}
      onOpenExternalLink={openLoopbackLink}
    >
      <div className="niuu:flex niuu:h-full niuu:flex-col" data-testid="live-session-detail-page">
        <div className="niuu-live-session__chrome">
          <div className="niuu-live-session__header">
            <div className="niuu-live-session__head-top">
              {returnTo ? (
                <button
                  type="button"
                  className="niuu-live-session__return-link"
                  aria-label="Back to Work"
                  onClick={() => void navigate({ to: returnTo as never })}
                >
                  <ArrowLeft aria-hidden="true" />
                  Work
                </button>
              ) : null}
              {onBackToUnread ? (
                <button
                  type="button"
                  className="niuu-live-session__return-link"
                  aria-label="Back to Unread"
                  onClick={onBackToUnread}
                  data-testid="back-to-unread"
                >
                  <ChevronLeft aria-hidden="true" />
                  Unread
                </button>
              ) : null}
              <span
                className={cn(
                  'niuu-live-session__status-dot',
                  isSessionConnected
                    ? 'niuu-live-session__status-dot--connected'
                    : 'niuu-live-session__status-dot--disconnected',
                  sessionWorking && 'niuu-live-session__status-dot--working',
                )}
                data-state={sessionDetail?.state}
                title={sessionDetail ? SESSION_STATE_LABELS[sessionDetail.state] : undefined}
              />
              <div className="niuu-live-session__head-title">
                <div className="niuu-live-session__identity">
                  <span className="niuu-live-session__title">{sessionName}</span>
                  {/* Lexi's title menu: the name's chevron opens the session's facts, its state
                      and rename, pin and move, so the bar itself stays one quiet line. */}
                  <details className="niuu-live-session__facts">
                    <summary aria-label="Session details" title="Session details">
                      <ChevronDown aria-hidden="true" />
                    </summary>
                    <div className="niuu-live-session__facts-panel">
                      {sessionDetail ? (
                        <span
                          className="niuu-live-session__state-pill"
                          data-tone={stateTone(sessionDetail.state)}
                          data-testid="session-state-pill"
                        >
                          {SESSION_STATE_LABELS[sessionDetail.state]}
                        </span>
                      ) : null}
                      {!readOnly && liveSession && (
                        <span className="niuu-live-session__title-actions">
                          <RenameSession sessionId={sessionId} name={sessionName} />
                          <PinSession sessionId={sessionId} name={sessionName} />
                          <AssignSessionProject
                            sessionId={sessionId}
                            name={sessionName}
                            instanceId={liveSession.instanceId}
                          />
                        </span>
                      )}
                      <Subline
                        data-testid="session-stats"
                        items={[
                          liveSession?.model && {
                            id: 'model',
                            node: (
                              <span
                                className="niuu-live-session__model"
                                data-harness={sessionEngineHarness}
                                title={liveSession.model}
                                data-testid="session-model"
                              >
                                {modelLabel(liveSession.model)}
                              </span>
                            ),
                          },
                          forgeBadgeLabel && {
                            id: 'host',
                            node: <span data-testid="session-host">{forgeBadgeLabel}</span>,
                          },
                          isCoordinator && {
                            id: 'coordinator',
                            node: (
                              <CoordinatorTag label={leaderTag(liveSession?.coordination?.role)} />
                            ),
                          },
                          sessionTrackerIssue && {
                            id: 'ticket',
                            node: <TicketLink issue={sessionTrackerIssue} />,
                          },
                          liveSession?.runId && {
                            id: 'run',
                            node: <RunLink runId={liveSession.runId} />,
                          },
                          sessionDetail && {
                            id: 'activity',
                            node: <SessionActivityElapsed session={sessionDetail} />,
                          },
                          showDetails && {
                            id: 'messages',
                            node: (
                              <span data-testid="session-message-count">
                                {formatCount(headerMessageCount)}{' '}
                                {headerMessageCount === 1 ? 'message' : 'messages'}
                              </span>
                            ),
                          },
                          showTokenUsage && {
                            id: 'tokens',
                            node: <span>{formatCount(liveSession?.tokensUsed ?? 0)} tokens</span>,
                          },
                          liveSession?.source && {
                            id: 'source',
                            node: (
                              <span className="niuu-live-session__source-line">
                                <SourceMeta session={liveSession} />
                                {liveSession.source.type === 'git' && liveSession.source.branch ? (
                                  <span
                                    className="niuu-live-session__branch"
                                    data-testid="session-branch"
                                    title={liveSession.source.branch}
                                  >
                                    <GitBranch aria-hidden="true" />
                                    {liveSession.source.branch}
                                  </span>
                                ) : null}
                              </span>
                            ),
                          },
                          showDetails &&
                            liveSession?.source && {
                              id: 'runtime',
                              node: (
                                <SessionRuntimeVersion session={liveSession} service={volundr} />
                              ),
                            },
                        ]}
                      />
                      {hasCoordinator ? (
                        <WorkerOf
                          coordinator={coordination.coordinator}
                          onOpen={openSession}
                          coordinated={isCoordinationRole(liveSession?.coordination?.role)}
                        />
                      ) : null}
                    </div>
                  </details>
                  {sessionProjectName ? (
                    <span
                      className="niuu-live-session__project"
                      data-testid="session-project"
                      title={`Project: ${sessionProjectName}`}
                    >
                      <Folder aria-hidden="true" />
                      {sessionProjectName}
                    </span>
                  ) : null}
                  {showDetails && sessionHandle ? (
                    <span className="niuu-live-session__handle" title={sessionHandle}>
                      {sessionHandle}
                    </span>
                  ) : null}
                  {showDetails && <SessionIdChip sessionId={sessionId} />}
                  {readOnly && sessionDetail?.state !== 'archived' ? (
                    <span className="niuu-live-session__archived-badge">Archived</span>
                  ) : null}
                </div>
              </div>

              {/* The views sit in the title bar as one segmented control, so the header is one
                  bar as Lexi's is. */}
              <div className="niuu-live-session__tabs" role="tablist" aria-label="Session tabs">
                {tabs.map((tab) =>
                  (() => {
                    const TabIcon = tab.icon;
                    return (
                      <button
                        key={tab.id}
                        id={`tab-${tab.id}`}
                        role="tab"
                        aria-selected={tabSelected(tab.id)}
                        onClick={() => openTab(tab.id)}
                        className={cn(
                          'niuu-live-session__tab',
                          tabSelected(tab.id) && 'niuu-live-session__tab--active',
                        )}
                      >
                        <TabIcon className="niuu-live-session__tab-icon" />
                        <span className="niuu-live-session__tab-label">{tab.label}</span>
                        <TabCountBadge count={tabCounts[tab.id]} />
                      </button>
                    );
                  })(),
                )}
              </div>
              <div className="niuu-live-session__toolbar">
                {previewPorts.map((port) => (
                  <button
                    key={port}
                    type="button"
                    className="niuu-live-session__preview-chip"
                    title={`Open port ${port} in the live preview`}
                    onClick={() => openPreviewPane(port)}
                  >
                    <Globe aria-hidden="true" />
                    Preview :{port}
                  </button>
                ))}
                <ContextMeter
                  context={contextMeter}
                  title={
                    contextMeter
                      ? `${formatCount(contextMeter.used)} of ${formatCount(contextMeter.window)} context used`
                      : undefined
                  }
                  cost={costValue}
                />
                {/* One Display options menu, as Lexi's: folding, tool calls and the rest. */}
                {resolvedActiveTab === 'chat' && (
                  <ChatDisplayControls
                    className="niuu-live-session__display-controls"
                    toolCalls={{
                      shown: showInternalMessages,
                      onToggle: handleToggleInternalMessages,
                    }}
                  />
                )}
                <SessionToolbarButton
                  icon={SquareTerminal}
                  title={terminalOpen ? 'Hide the terminal (⌃`)' : 'Show the terminal (⌃`)'}
                  active={terminalOpen}
                  onClick={toggleTerminal}
                />
                <SessionToolbarButton
                  icon={Columns2}
                  title={dockOpen ? 'Hide the dock (⌥⌘→)' : 'Show the dock (⌥⌘→)'}
                  active={dockOpen}
                  onClick={() => dispatchDock({ type: 'toggle' })}
                />
                {panelHasContent ? (
                  <SessionToolbarButton
                    icon={PanelRight}
                    title={panelShown ? 'Hide session panel' : 'Show session panel'}
                    active={panelShown}
                    tone={!panelShown && attentionItems.length > 0 ? 'brand' : 'neutral'}
                    label={
                      !panelShown && attentionItems.length > 0
                        ? `Needs you ${attentionItems.length}`
                        : undefined
                    }
                    onClick={() => {
                      // The panel and the dock share the right side: opening one closes the other.
                      dispatchDock({ type: 'hide' });
                      setPanelOpen(!panelShown);
                    }}
                  />
                ) : null}
                {primaryStateAction ? (
                  <SessionToolbarButton
                    icon={primaryStateAction.icon}
                    title={primaryStateAction.title}
                    label={primaryStateAction.label}
                    onClick={primaryStateAction.onSelect}
                    disabled={actionBusy !== null || primaryStateAction.disabled === true}
                    tone={primaryStateAction.tone}
                  />
                ) : null}
                <SessionMoreMenu items={moreMenuItems} />
              </div>
            </div>
          </div>
        </div>

        {forwardedNotice && (
          <div
            role="status"
            className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-4 niuu:py-2 niuu:text-xs niuu:text-text-secondary"
            data-testid="session-forwarded-notice"
          >
            <span>Forwarded to {forwardedNotice.targetName}.</span>
            <button
              type="button"
              className="niuu:text-text-muted niuu:hover:text-text-primary"
              onClick={() => setForwardedNotice(null)}
              aria-label="Dismiss"
            >
              ✕
            </button>
          </div>
        )}
        <ForwardMessageDialog
          open={forwarding !== null}
          onOpenChange={(next) => !next && setForwarding(null)}
          currentSessionId={sessionId}
          currentSessionName={sessionName}
          content={forwarding?.content ?? ''}
          onDelivered={(notice) => {
            setForwarding(null);
            setForwardedNotice(notice);
          }}
        />

        {actionError || sessionRead.error ? (
          <div
            role="alert"
            className="niuu:border-b niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-4 niuu:py-2 niuu:text-xs niuu:text-critical"
            data-testid="session-action-error"
          >
            <LinkedText text={actionError ?? sessionRead.error ?? ''} />
          </div>
        ) : liveSession?.error && (sessionStatus === 'failed' || sessionStatus === 'error') ? (
          <div
            role="alert"
            className="niuu:border-b niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-4 niuu:py-2 niuu:text-xs niuu:text-critical"
            data-testid="session-failure-reason"
          >
            Session failed: <LinkedText text={liveSession.error} />
          </div>
        ) : liveSession?.error && isSessionBooting(sessionStatus) && !liveSession.startup ? (
          <div
            role="status"
            className="niuu:border-b niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-4 niuu:py-2 niuu:text-xs niuu:text-secondary"
            data-testid="session-provisioning-detail"
          >
            Provisioning: <LinkedText text={liveSession.error} />
          </div>
        ) : null}

        <div ref={setWorkArea} className="niuu:flex niuu:min-h-0 niuu:flex-1 niuu:flex-col">
          <div
            ref={setSessionRow}
            className="niuu:relative niuu:flex niuu:min-h-0 niuu:flex-1 niuu:overflow-hidden"
          >
            <div className="niuu:min-h-0 niuu:min-w-0 niuu:flex-1 niuu:overflow-hidden">
              {resolvedActiveTab === 'chat' && (
                <SessionLinksProvider value={sessionLinks}>
                  <SecretRequestsContext.Provider value={secretRequests}>
                    <div
                      role="tabpanel"
                      className="niuu:flex niuu:h-full niuu:min-h-0 niuu:flex-col"
                    >
                      {isReady && chatEndpoint ? (
                        <>
                          <SessionChat
                            className="niuu:h-full"
                            showToolbar={false}
                            showDisplayControls={false}
                            showWorkersStrip={!workersInList}
                            aboveComposer={decisionDock}
                            showInternalToggle={false}
                            internalVisibility={showInternalMessages}
                            showTokenUsage={showTokenUsage}
                            messages={chat.messages}
                            streamingContent={chat.streamingContent}
                            streamingParts={chat.streamingParts}
                            streamingModel={chat.streamingModel}
                            connected={chat.connected}
                            historyLoaded={chat.historyLoaded}
                            hasOlderHistory={chat.hasOlderHistory}
                            loadingOlderHistory={chat.loadingOlderHistory}
                            olderHistoryError={chat.olderHistoryError}
                            onLoadOlderHistory={chat.loadOlderHistory}
                            historyError={chat.historyError}
                            onRetryHistory={chat.retryHistory}
                            participants={roomParticipants}
                            hostPeerId={sessionId}
                            hostLabel={leaderTag(liveSession?.coordination?.role)}
                            workerSessionIds={workerSessionIds}
                            focusedWorkerId={focusedWorkerId}
                            onFocusWorker={onFocusWorker}
                            onWorkersChange={onRoomWorkers}
                            meshEvents={chat.meshEvents}
                            agentEvents={chat.agentEvents}
                            pendingPermissions={chat.pendingPermissions}
                            pendingInputRequests={chat.pendingInputRequests}
                            availableCommands={chat.availableCommands}
                            capabilities={chat.capabilities}
                            chatEndpoint={chatEndpoint}
                            historyEndpoint={historyEndpoint}
                            sessionName={sessionName}
                            onSend={chat.sendMessage}
                            onForwardMessage={setForwarding}
                            onSendDirected={chat.sendDirectedMessages}
                            onStop={chat.sendInterrupt}
                            onClear={chat.clearMessages}
                            onSetInternalVisibility={chat.sendSetInternalVisibility}
                            onSetModel={chat.sendSetModel}
                            onSetThinkingTokens={chat.sendSetThinkingTokens}
                            plan={chat.plan}
                            agents={chat.agents}
                            effort={chat.effort}
                            runtimeOptions={chat.runtimeOptions}
                            onSetEffort={chat.sendSetEffort}
                            onSetRuntimeModel={(model) => chat.sendSetRuntimeOptions({ model })}
                            runtimeOptionsError={chat.runtimeOptionsError}
                            effortError={chat.effortError}
                            working={sessionWorking}
                            onRewindFiles={chat.sendRewindFiles}
                            onPermissionRespond={chat.respondToPermission}
                            onInputRespond={chat.respondToInput}
                            onInputDismiss={chat.dismissInputRequest}
                            onFetchFiles={fetchSessionMentionFiles}
                            onMessageCountChange={setVisibleMessageCount}
                            renderPermissions={permissionRenderer}
                          />
                        </>
                      ) : failedBeforeStart && liveSession?.startup ? (
                        <div className="niuu:flex niuu:h-full niuu:items-center niuu:justify-center niuu:overflow-auto niuu:p-6">
                          <SessionStartupProgress
                            startup={liveSession.startup}
                            status={sessionStatus}
                            error={liveSession.error}
                          />
                        </div>
                      ) : failedBeforeStart ? (
                        <div
                          className="niuu:flex niuu:h-full niuu:items-center niuu:justify-center niuu:text-sm niuu:text-text-muted"
                          data-testid="failed-before-start"
                        >
                          This session failed before it started, so it has no transcript.
                        </div>
                      ) : canReplayTranscript && transcriptQuery.isLoading ? (
                        <div className="niuu:flex niuu:h-full niuu:items-center niuu:justify-center niuu:text-sm niuu:text-text-muted">
                          Loading saved transcript…
                        </div>
                      ) : canReplayTranscript && transcriptQuery.isError ? (
                        <div className="niuu:flex niuu:h-full niuu:items-center niuu:justify-center niuu:px-6 niuu:text-center niuu:text-sm niuu:text-critical">
                          Failed to load saved transcript:{' '}
                          {transcriptQuery.error instanceof Error
                            ? transcriptQuery.error.message
                            : String(transcriptQuery.error)}
                        </div>
                      ) : canReplayTranscript && replayMessages.length > 0 ? (
                        <SessionChat
                          className="niuu:h-full"
                          showToolbar={false}
                          showDisplayControls={false}
                          showWorkersStrip={!workersInList}
                          showInternalToggle={false}
                          internalVisibility={showInternalMessages}
                          showTokenUsage={showTokenUsage}
                          messages={replayMessages}
                          chatEndpoint={chatEndpoint}
                          connected={false}
                          historyLoaded={!transcriptQuery.isLoading}
                          participants={replayRoomParticipants}
                          hostPeerId={sessionId}
                          hostLabel={leaderTag(liveSession?.coordination?.role)}
                          workerSessionIds={workerSessionIds}
                          focusedWorkerId={focusedWorkerId}
                          onFocusWorker={onFocusWorker}
                          onWorkersChange={onRoomWorkers}
                          meshEvents={replayMeshEvents}
                          plan={chat.plan}
                          agents={chat.agents}
                          working={false}
                          sessionName={sessionName}
                          onMessageCountChange={setVisibleMessageCount}
                          onSend={() => {}}
                          onStop={() => {}}
                        />
                      ) : isSessionBooting(sessionStatus) && liveSession?.startup ? (
                        <div className="niuu:flex niuu:h-full niuu:items-center niuu:justify-center niuu:overflow-auto niuu:p-6">
                          <SessionStartupProgress
                            startup={liveSession.startup}
                            status={sessionStatus}
                            error={liveSession.error}
                          />
                        </div>
                      ) : isSessionBooting(sessionStatus) ? (
                        <div className="niuu:flex niuu:h-full niuu:items-center niuu:justify-center niuu:text-sm niuu:text-text-muted">
                          Session is starting…
                        </div>
                      ) : canReplayTranscript ? (
                        <div className="niuu:flex niuu:h-full niuu:items-center niuu:justify-center niuu:text-sm niuu:text-text-muted">
                          No saved transcript yet.
                        </div>
                      ) : (
                        <div className="niuu:flex niuu:h-full niuu:items-center niuu:justify-center niuu:text-sm niuu:text-text-muted">
                          Start the session to chat.
                        </div>
                      )}
                    </div>
                  </SecretRequestsContext.Provider>
                </SessionLinksProvider>
              )}

              {resolvedActiveTab === 'diffs' && (
                <div role="tabpanel" className="niuu:h-full niuu:min-h-0">
                  <SessionDiffs
                    chatEndpoint={chatEndpoint}
                    review={readOnly ? undefined : review}
                  />
                </div>
              )}

              {resolvedActiveTab === 'files' && (
                <div role="tabpanel" className="niuu:h-full niuu:min-h-0">
                  <SessionFilesWorkspace
                    sessionId={sessionId}
                    filesystem={filesystem}
                    review={readOnly ? undefined : review}
                  />
                </div>
              )}

              {resolvedActiveTab === 'chronicles' && (
                <div role="tabpanel" className="niuu:h-full niuu:min-h-0">
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
                  className="niuu-live-telemetry-panel niuu:h-full niuu:min-h-0 niuu:overflow-auto"
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
                <div role="tabpanel" className="niuu:h-full niuu:min-h-0">
                  <LiveLogsTab sessionId={sessionId} volundr={volundr} />
                </div>
              )}
            </div>
            {shownPane && dockShown ? (
              <RightDock
                tabs={dockTabs}
                active={dock.active}
                width={dockWidth}
                edge={
                  <SidebarResizer
                    label="Resize the dock"
                    panelSide="right"
                    {...dockResizer}
                    width={dockWidth}
                  />
                }
                addItems={dockAddItems}
                onSelect={(id) => dispatchDock({ type: 'select', id })}
                onClosePane={(id) => dispatchDock({ type: 'close', id })}
                onClose={() => dispatchDock({ type: 'hide' })}
              >
                {shownPane.kind === 'changes' ? (
                  <SessionDiffs
                    chatEndpoint={chatEndpoint}
                    review={readOnly ? undefined : review}
                    layout="dock"
                  />
                ) : shownPane.kind === 'files' ? (
                  <FilesPane
                    key={filesFocus}
                    sessionId={sessionId}
                    filesystem={filesystem}
                    touched={touchedPaths}
                    initialPath={filesFocus}
                    onManage={
                      readOnly
                        ? undefined
                        : () => {
                            dispatchDock({ type: 'hide' });
                            setTabWasManuallySelected(true);
                            setActiveTab('files');
                          }
                    }
                  />
                ) : shownPane.kind === 'file' ? (
                  <FilePreviewPane
                    key={shownPane.path}
                    sessionId={sessionId}
                    path={shownPane.path}
                    filesystem={filesystem}
                    onOpenInFiles={() => {
                      setFilesFocus(shownPane.path);
                      openPane({ kind: 'files' });
                    }}
                  />
                ) : shownPane.kind === 'preview' ? (
                  <LivePreviewPane
                    key={`${shownPane.port ?? ''}:${shownPane.path}`}
                    sessionId={sessionId}
                    config={livePreview}
                    ports={previewPorts}
                    port={shownPane.port}
                    initialPath={shownPane.path}
                    onSelectPort={(port) => openPreviewPane(port)}
                  />
                ) : liveSession ? (
                  <SideChatPane
                    session={liveSession}
                    childId={shownPane.childId}
                    readOnly={readOnly}
                    onForked={(childId) => dispatchDock({ type: 'side', childId })}
                    onNewChat={sideNewChat}
                    onOpenSession={openSession}
                  />
                ) : (
                  <LoadingState className="forge-session-loading" label="Loading the session…" />
                )}
              </RightDock>
            ) : null}
            {!dockOpen && showPanel && panelHasContent ? (
              <SessionAttentionPanel
                items={attentionItems}
                ticket={sessionTrackerIssue}
                checklist={checklist}
                agents={chat.agents}
                changedFiles={tabCounts.diffs}
                fileStats={sessionDetail?.files}
                deliverables={presentedFiles}
                info={infoRows}
                workerSessions={coordination.workers}
                ravnWorkers={ravnWorkers}
                onOpenSession={openSession}
                showOpenChat={resolvedActiveTab !== 'chat'}
                onOpenChat={() => openTab('chat')}
                onOpenDiffs={() => openTab('diffs')}
                onRespondPermission={chat.respondToPermission}
                onClose={() => setPanelOpen(false)}
                overlay={narrowScreen}
              />
            ) : null}
          </div>
          {terminalShown ? (
            <TerminalDock
              height={terminalHeight}
              edge={
                <SidebarResizer
                  label="Resize the terminal"
                  panelSide="bottom"
                  {...terminalResizer}
                  width={terminalHeight}
                />
              }
            >
              <SessionTerminalLive
                url={isReady ? terminalUrl : null}
                readOnly={readOnly}
                onClose={() => setTerminalPreference('0')}
                unavailableMessage={
                  isSessionBooting(sessionStatus)
                    ? 'Session is starting…'
                    : 'Start the session to use its terminal.'
                }
              />
            </TerminalDock>
          ) : null}
        </div>
        <QuickOpenFiles
          open={quickOpen}
          onOpenChange={setQuickOpen}
          candidates={quickOpenCandidates}
          touched={touchedPaths}
          loadErrors={[
            quickOpenChangesQuery.isError
              ? `The changed files could not be listed: ${errorText(quickOpenChangesQuery.error)}`
              : null,
            quickOpenTreeQuery.isError
              ? `The workspace could not be listed: ${errorText(quickOpenTreeQuery.error)}`
              : null,
          ].filter((message): message is string => message !== null)}
          onPick={(path) => {
            setQuickOpen(false);
            openFilePane(path);
          }}
        />

        {review.comments.length > 0 && !activeHumanGate ? (
          <ReviewBar
            count={review.comments.length}
            canSend={isSessionConnected}
            onSend={handleSendReview}
            onDiscard={review.clear}
          />
        ) : null}

        <DeleteSessionDialog
          open={deleteDialogOpen}
          session={liveSession ?? null}
          onClose={() => setDeleteDialogOpen(false)}
          onConfirm={(cleanup) => void handleDeleteSession(cleanup)}
          busy={actionBusy === 'delete'}
        />
      </div>
    </SessionResources>
  );
}
