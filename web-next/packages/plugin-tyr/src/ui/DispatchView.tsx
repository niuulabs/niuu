import { useMemo, useState } from 'react';
import { useService } from '@niuulabs/plugin-sdk';
import {
  StateDot,
  ConfidenceBar,
  Tooltip,
  TooltipProvider,
  ToastProvider,
  useToast,
  SegmentedFilter,
  cn,
} from '@niuulabs/ui';
import type { SegmentedFilterOption } from '@niuulabs/ui';
import type { IDispatchBus, IDispatcherService } from '../ports';
import type { RaidStatus } from '../domain/saga';
import type { Workflow } from '../domain/workflow';
import { checkFeasibility, type FeasibilityResult } from '../application/dispatch-feasibility';
import { useDispatcherState } from './useDispatcherState';
import { useDispatchQueue, type DispatchEntry } from './useDispatchQueue';
import { WorkflowOverrideModal } from './WorkflowOverrideModal';
import { ThresholdOverrideModal } from './ThresholdOverrideModal';
import { EditRulesModal, type RulesFormState } from './EditRulesModal';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_MAX_RETRIES = 3;

const MOCK_RECENT_DISPATCHES: { id: string; workflow: string; time: string }[] = [
  { id: 'NIU-214.2', workflow: 'ship', time: '13:42' },
  { id: 'NIU-199.2', workflow: 'ship', time: '13:28' },
  { id: 'NIU-183.4', workflow: 'deep-review', time: '13:11' },
  { id: 'NIU-214.1', workflow: 'ship', time: '12:49' },
  { id: 'NIU-199.1', workflow: 'ship', time: '12:30' },
];

// ---------------------------------------------------------------------------
// Filter types
// ---------------------------------------------------------------------------

type StatusFilter = 'all' | 'ready' | 'blocked' | 'queue';

const FILTER_LABELS: Record<StatusFilter, string> = {
  all: 'All',
  ready: 'Ready',
  blocked: 'Blocked',
  queue: 'Queue',
};

const QUEUE_STATUSES: RaidStatus[] = ['queued', 'running'];
const TERMINAL_STATUSES: RaidStatus[] = ['merged', 'failed', 'review', 'escalated'];

// ---------------------------------------------------------------------------
// Enriched entry
// ---------------------------------------------------------------------------

interface EnrichedEntry extends DispatchEntry {
  feasibility: FeasibilityResult;
  effectiveStatus: RaidStatus;
}

// ---------------------------------------------------------------------------
// Gate chips
// ---------------------------------------------------------------------------

const GATE_LABELS: Record<string, string> = {
  raven_resolution: 'no raven',
  confidence: 'low conf',
  upstream_blocked: 'upstream',
  cluster_healthy: 'cluster',
};

// ---------------------------------------------------------------------------
// Filter options builder
// ---------------------------------------------------------------------------

function buildFilterOptions(
  counts: Record<StatusFilter, number>,
): SegmentedFilterOption<StatusFilter>[] {
  return (Object.keys(FILTER_LABELS) as StatusFilter[]).map((key) => ({
    value: key,
    label: FILTER_LABELS[key],
    count: counts[key],
  }));
}

// ---------------------------------------------------------------------------
// Batch dispatch bar
// ---------------------------------------------------------------------------

function BatchDispatchBar({
  selectedCount,
  canDispatch,
  onDispatch,
  isDispatching,
  onApplyWorkflow,
  onOverrideThreshold,
}: {
  selectedCount: number;
  canDispatch: boolean;
  onDispatch: () => void;
  isDispatching: boolean;
  onApplyWorkflow?: () => void;
  onOverrideThreshold?: () => void;
}) {
  if (selectedCount === 0) return null;

  return (
    <div
      className="niuu-flex niuu-items-center niuu-gap-3 niuu-px-4 niuu-py-2 niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary"
      role="status"
      aria-live="polite"
    >
      <span className="niuu-text-sm niuu-font-medium niuu-text-text-primary">{selectedCount}</span>
      <span className="niuu-text-sm niuu-text-text-secondary">selected</span>
      <span className="niuu-flex-1" />
      {onApplyWorkflow && (
        <button
          type="button"
          onClick={onApplyWorkflow}
          className="niuu-py-1 niuu-px-3 niuu-bg-bg-secondary niuu-text-text-secondary niuu-border niuu-border-border niuu-rounded-sm niuu-cursor-pointer niuu-font-mono niuu-text-xs"
        >
          Apply workflow…
        </button>
      )}
      {onOverrideThreshold && (
        <button
          type="button"
          onClick={onOverrideThreshold}
          className="niuu-py-1 niuu-px-3 niuu-bg-bg-secondary niuu-text-text-secondary niuu-border niuu-border-border niuu-rounded-sm niuu-cursor-pointer niuu-font-mono niuu-text-xs"
        >
          Override threshold
        </button>
      )}
      <Tooltip content={canDispatch ? undefined : 'Select only ready raids to dispatch'} side="top">
        <button
          type="button"
          onClick={() => {
            if (!canDispatch || isDispatching) return;
            onDispatch();
          }}
          className={cn(
            'niuu-py-1 niuu-px-3 niuu-rounded-sm niuu-font-mono niuu-text-xs niuu-border niuu-cursor-pointer',
            canDispatch && !isDispatching
              ? 'niuu-bg-brand niuu-text-bg-primary niuu-border-brand'
              : 'niuu-cursor-not-allowed niuu-opacity-50 niuu-bg-bg-tertiary niuu-text-text-muted niuu-border-border-subtle',
          )}
          aria-disabled={!canDispatch || isDispatching}
        >
          {isDispatching ? 'Dispatching…' : '⚡ Dispatch now'}
        </button>
      </Tooltip>
    </div>
  );
}

function PendingDispatchBar({ entries }: { entries: EnrichedEntry[] }) {
  if (entries.length === 0) return null;

  return (
    <div
      className="niuu-border-b niuu-border-brand/30 niuu-bg-[linear-gradient(90deg,rgba(56,189,248,0.10),rgba(59,130,246,0.06))] niuu-px-4 niuu-py-3"
      role="status"
      aria-live="polite"
    >
      <div className="niuu-flex niuu-items-start niuu-gap-3">
        <span className="niuu-mt-1 niuu-inline-flex niuu-h-2.5 niuu-w-2.5 niuu-shrink-0 niuu-rounded-full niuu-bg-brand niuu-animate-pulse" />
        <div className="niuu-min-w-0 niuu-flex-1">
          <div className="niuu-text-sm niuu-font-semibold niuu-text-text-primary">
            Dispatching {entries.length} raid{entries.length === 1 ? '' : 's'}
          </div>
          <div className="niuu-mt-1 niuu-text-xs niuu-text-text-secondary">
            Keeping this batch visible while Tyr submits it.
          </div>
          <div className="niuu-mt-3 niuu-flex niuu-flex-col niuu-gap-2">
            {entries.map((entry) => (
              <div
                key={entry.raid.id}
                className="niuu-flex niuu-items-center niuu-gap-2 niuu-rounded-md niuu-border niuu-border-brand/20 niuu-bg-bg-primary/80 niuu-px-3 niuu-py-2"
              >
                <span className="niuu-rounded niuu-bg-bg-elevated niuu-px-1.5 niuu-py-0.5 niuu-font-mono niuu-text-[10px] niuu-text-text-secondary">
                  {entry.raid.trackerId}
                </span>
                <span className="niuu-min-w-0 niuu-flex-1 niuu-truncate niuu-text-sm niuu-text-text-primary">
                  {entry.raid.name}
                </span>
                <span className="niuu-rounded-sm niuu-border niuu-border-brand/30 niuu-bg-brand/10 niuu-px-2 niuu-py-0.5 niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-text-brand">
                  submitting
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Saga group header
// ---------------------------------------------------------------------------

function SagaGroupHeader({
  sagaName,
  trackerId,
  featureBranch,
  raidCount,
  workflowName,
}: {
  sagaName: string;
  trackerId: string;
  featureBranch: string;
  raidCount: number;
  workflowName?: string;
}) {
  return (
    <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-px-4 niuu-py-2 niuu-bg-bg-tertiary niuu-border-b niuu-border-border-subtle niuu-sticky niuu-top-0 niuu-z-10">
      <span className="niuu-text-xs niuu-font-mono niuu-text-text-muted niuu-bg-bg-elevated niuu-px-1.5 niuu-py-0.5 niuu-rounded">
        {trackerId}
      </span>
      <span className="niuu-text-sm niuu-font-medium niuu-text-text-primary">{sagaName}</span>
      <span className="niuu-text-xs niuu-font-mono niuu-text-text-muted niuu-ml-auto niuu-flex niuu-items-center niuu-gap-2">
        <span>
          {raidCount} queued · {featureBranch}
        </span>
        {workflowName && (
          <>
            <span className="niuu-w-px niuu-h-[10px] niuu-bg-border" />
            <span>
              workflow <span className="niuu-text-text-secondary">{workflowName}</span>
            </span>
          </>
        )}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Raid row (within saga group)
// ---------------------------------------------------------------------------

function RaidRow({
  entry,
  isSelected,
  onToggle,
  workflowName,
  isPendingDispatch = false,
}: {
  entry: EnrichedEntry;
  isSelected: boolean;
  onToggle: () => void;
  workflowName?: string;
  isPendingDispatch?: boolean;
}) {
  const waitMin = Math.round((Date.now() - new Date(entry.raid.updatedAt).getTime()) / 60_000);
  const waitLabel = waitMin <= 1 ? 'now' : `${waitMin}m wait`;

  return (
    <div
      className={cn(
        'niuu-flex niuu-items-center niuu-gap-3 niuu-px-4 niuu-py-2.5 niuu-border niuu-rounded-md niuu-cursor-pointer niuu-transition-colors',
        isPendingDispatch
          ? 'niuu-bg-[linear-gradient(90deg,rgba(56,189,248,0.10),rgba(59,130,246,0.04))] niuu-border-brand/30'
          : isSelected
            ? 'niuu-bg-[#1e232a] niuu-border-border'
            : 'niuu-bg-[#171b22] niuu-border-border-subtle hover:niuu-bg-[#1b2028]',
      )}
      onClick={onToggle}
    >
      <label
        className={cn(
          'niuu-w-5 niuu-h-5 niuu-rounded-sm niuu-border niuu-flex niuu-items-center niuu-justify-center niuu-shrink-0 niuu-text-[10px] niuu-cursor-pointer',
          isPendingDispatch
            ? 'niuu-bg-brand/15 niuu-border-brand/40 niuu-text-brand'
            : isSelected
              ? 'niuu-bg-brand niuu-border-brand niuu-text-bg-primary'
              : 'niuu-bg-bg-tertiary niuu-border-border',
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <input
          type="checkbox"
          checked={isSelected || isPendingDispatch}
          onChange={onToggle}
          className="niuu-sr-only"
          aria-label="Select row"
          disabled={isPendingDispatch}
        />
        {(isSelected || isPendingDispatch) && '✓'}
      </label>
      <div className="niuu-flex-1 niuu-min-w-0">
        <div className="niuu-text-sm niuu-font-medium niuu-text-text-primary niuu-truncate">
          {entry.raid.name}
        </div>
        <div className="niuu-text-[10px] niuu-font-mono niuu-text-text-muted niuu-mt-0.5">
          {entry.raid.trackerId}
          {entry.raid.estimateHours != null && ` · est ${entry.raid.estimateHours}h`}
          {entry.raid.retryCount != null &&
            entry.raid.retryCount > 0 &&
            ` · retry ${entry.raid.retryCount}`}
        </div>
      </div>
      <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-shrink-0">
        {workflowName && (
          <span
            className="niuu-rounded-sm niuu-bg-bg-elevated niuu-px-1.5 niuu-py-0.5 niuu-font-mono niuu-text-xs niuu-text-brand niuu-border niuu-border-brand/30"
            aria-label={`workflow override: ${workflowName}`}
          >
            {workflowName}
          </span>
        )}
        {isPendingDispatch && (
          <span className="niuu-inline-flex niuu-items-center niuu-rounded-sm niuu-border niuu-border-brand/30 niuu-bg-brand/10 niuu-px-2 niuu-py-0.5 niuu-text-xs niuu-font-mono niuu-uppercase niuu-text-brand">
            submitting
          </span>
        )}
        {entry.feasibility.feasible ? (
          <span
            className="niuu-inline-flex niuu-items-center niuu-rounded-sm niuu-px-2 niuu-py-0.5 niuu-text-xs niuu-font-mono niuu-uppercase"
            style={{ border: '1px solid rgba(16,185,129,0.5)', color: 'rgb(52,211,153)' }}
          >
            ready
          </span>
        ) : (
          <span
            className="niuu-inline-flex niuu-items-center niuu-rounded-sm niuu-px-2 niuu-py-0.5 niuu-text-xs niuu-font-mono niuu-uppercase"
            style={{ border: '1px solid rgba(168,85,247,0.5)', color: 'rgb(192,132,252)' }}
          >
            blocked
            {entry.feasibility.gates.filter((g) => !g.passed).length > 0 &&
              ` · ${entry.feasibility.gates
                .filter((g) => !g.passed)
                .map((g) => GATE_LABELS[g.name] ?? g.name)
                .join(', ')}`}
          </span>
        )}
        <ConfidenceBar
          level={
            entry.raid.confidence >= 80 ? 'high' : entry.raid.confidence >= 50 ? 'medium' : 'low'
          }
          hideLabel
        />
        <span className="niuu-text-xs niuu-font-mono niuu-text-text-muted niuu-w-6 niuu-text-right">
          {entry.raid.confidence}
        </span>
        <span className="niuu-text-[10px] niuu-font-mono niuu-text-text-muted niuu-w-[60px] niuu-text-right">
          {waitLabel}
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Right panel: dispatch rules + recent dispatches
// ---------------------------------------------------------------------------

function DispatchRulesPanel({
  threshold,
  maxConcurrentRaids,
  autoContinue,
  retryCount,
  onEdit,
}: {
  threshold: number;
  maxConcurrentRaids: number;
  autoContinue: boolean;
  retryCount: number;
  onEdit: () => void;
}) {
  const rules = [
    { label: 'Confidence threshold', value: `≥ ${(threshold / 100).toFixed(2)}` },
    { label: 'Max concurrent', value: String(maxConcurrentRaids) },
    { label: 'Auto-continue', value: autoContinue ? 'on' : 'off' },
    { label: 'Retry on fail', value: `up to ${retryCount}` },
    { label: 'Quiet hours', value: '22:00 – 07:00' },
  ];

  return (
    <div className="niuu-p-4 niuu-flex niuu-flex-col niuu-gap-4">
      {/* Dispatch rules card */}
      <div
        className="niuu-rounded-lg niuu-border niuu-border-border niuu-bg-bg-secondary niuu-p-4"
        aria-label="Dispatch rules"
      >
        <div className="niuu-flex niuu-items-center niuu-justify-between niuu-mb-3">
          <h3 className="niuu-m-0 niuu-text-sm niuu-font-semibold niuu-text-text-primary">
            Dispatch rules
          </h3>
          <button
            type="button"
            onClick={onEdit}
            className="niuu-py-1 niuu-px-3 niuu-bg-bg-secondary niuu-text-text-secondary niuu-border niuu-border-border niuu-rounded-sm niuu-cursor-pointer niuu-font-mono niuu-text-xs"
          >
            Edit
          </button>
        </div>
        <dl className="niuu-flex niuu-flex-col niuu-gap-2 niuu-text-xs niuu-m-0">
          {rules.map((r) => (
            <div key={r.label} className="niuu-flex niuu-justify-between niuu-items-baseline">
              <dt className="niuu-text-text-muted">{r.label}</dt>
              <dd className="niuu-m-0 niuu-font-mono niuu-text-text-primary niuu-font-semibold">
                {r.value}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      {/* Recent dispatches card */}
      <div className="niuu-rounded-lg niuu-border niuu-border-border niuu-bg-bg-secondary niuu-p-4">
        <h3 className="niuu-m-0 niuu-mb-3 niuu-text-sm niuu-font-semibold niuu-text-text-primary">
          Recent dispatches
        </h3>
        <div>
          {MOCK_RECENT_DISPATCHES.map((d, i) => (
            <div
              key={d.id}
              className={cn(
                'niuu-grid niuu-grid-cols-[auto_1fr_auto] niuu-gap-2 niuu-py-1.5 niuu-text-xs',
                i > 0 && 'niuu-border-t niuu-border-border-subtle',
              )}
            >
              <span className="niuu-rounded niuu-bg-bg-elevated niuu-px-1.5 niuu-py-0.5 niuu-font-mono niuu-text-text-secondary">
                {d.id}
              </span>
              <span className="niuu-font-mono niuu-text-text-muted">wf: {d.workflow}</span>
              <span className="niuu-font-mono niuu-text-text-muted">{d.time}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inner content (needs Toast context)
// ---------------------------------------------------------------------------

function DispatchViewContent() {
  const dispatcherQuery = useDispatcherState();
  const queueQuery = useDispatchQueue();
  const dispatchBus = useService<IDispatchBus>('tyr.dispatch');
  const dispatcherService = useService<IDispatcherService>('tyr.dispatcher');
  const { toast } = useToast();

  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [optimisticQueued, setOptimisticQueued] = useState<Set<string>>(new Set());
  const [pendingDispatchEntries, setPendingDispatchEntries] = useState<EnrichedEntry[]>([]);
  const [isDispatching, setIsDispatching] = useState(false);
  const [isPausing, setIsPausing] = useState(false);

  // Modal visibility
  const [showWorkflowModal, setShowWorkflowModal] = useState(false);
  const [showThresholdModal, setShowThresholdModal] = useState(false);
  const [showEditRulesModal, setShowEditRulesModal] = useState(false);

  // Local overrides — null = use server state
  const [thresholdOverride, setThresholdOverride] = useState<number | null>(null);
  const [workflowOverride, setWorkflowOverride] = useState<Map<string, Workflow>>(new Map());
  const [rulesOverride, setRulesOverride] = useState<{
    maxConcurrentRaids: number;
    autoContinue: boolean;
    retryCount: number;
  } | null>(null);

  const dispatcherState = dispatcherQuery.data ?? null;

  // Effective display values (server state + local overrides)
  const effectiveThreshold = thresholdOverride ?? dispatcherState?.threshold ?? 70;
  const effectiveMaxConcurrent =
    rulesOverride?.maxConcurrentRaids ?? dispatcherState?.maxConcurrentRaids ?? 3;
  const effectiveAutoContinue =
    rulesOverride?.autoContinue ?? dispatcherState?.autoContinue ?? false;
  const effectiveRetryCount = rulesOverride?.retryCount ?? DEFAULT_MAX_RETRIES;

  // Enrich each entry with feasibility + optimistic status
  const enriched: EnrichedEntry[] = useMemo(() => {
    const entries = queueQuery.data ?? [];
    if (!dispatcherState) return [];

    return entries.map((entry) => {
      const effectiveStatus: RaidStatus = optimisticQueued.has(entry.raid.id)
        ? 'queued'
        : entry.raid.status;

      const feasibility = checkFeasibility({
        raid: { ...entry.raid, status: effectiveStatus },
        phase: entry.phase,
        allPhasesForSaga: entry.allPhases,
        dispatcherState,
        ravenResolved: true,
        clusterHealthy: true,
      });

      return { ...entry, feasibility, effectiveStatus };
    });
  }, [queueQuery.data, dispatcherState, optimisticQueued]);

  // Apply filter
  const filtered = useMemo(() => {
    let result = enriched;

    switch (statusFilter) {
      case 'ready':
        result = result.filter(
          (e) => e.feasibility.feasible && !QUEUE_STATUSES.includes(e.effectiveStatus),
        );
        break;
      case 'blocked':
        result = result.filter(
          (e) => !e.feasibility.feasible && !QUEUE_STATUSES.includes(e.effectiveStatus),
        );
        break;
      case 'queue':
        result = result.filter((e) => QUEUE_STATUSES.includes(e.effectiveStatus));
        break;
      default:
        result = result.filter((e) => !TERMINAL_STATUSES.includes(e.effectiveStatus));
    }

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter(
        (e) =>
          e.raid.name.toLowerCase().includes(q) ||
          e.saga.name.toLowerCase().includes(q) ||
          e.phase.name.toLowerCase().includes(q),
      );
    }

    return result;
  }, [enriched, statusFilter, searchQuery]);

  // Counts per tab
  const counts = useMemo((): Record<StatusFilter, number> => {
    const ready = enriched.filter(
      (e) => e.feasibility.feasible && !QUEUE_STATUSES.includes(e.effectiveStatus),
    ).length;
    const blocked = enriched.filter(
      (e) => !e.feasibility.feasible && !QUEUE_STATUSES.includes(e.effectiveStatus),
    ).length;
    const queue = enriched.filter((e) => QUEUE_STATUSES.includes(e.effectiveStatus)).length;
    return { all: ready + blocked + queue, ready, blocked, queue };
  }, [enriched]);

  // Group filtered entries by sagaId
  const groupedBySaga = useMemo(() => {
    const map = new Map<
      string,
      {
        sagaName: string;
        trackerId: string;
        featureBranch: string;
        workflowName: string;
        entries: EnrichedEntry[];
      }
    >();
    for (const entry of filtered) {
      const existing = map.get(entry.saga.id);
      if (existing) {
        existing.entries.push(entry);
      } else {
        map.set(entry.saga.id, {
          sagaName: entry.saga.name,
          trackerId: entry.saga.trackerId,
          featureBranch: entry.saga.featureBranch,
          workflowName: entry.saga.workflow ?? '',
          entries: [entry],
        });
      }
    }
    return Array.from(map.entries());
  }, [filtered]);

  const selectedEntries = filtered.filter((e) => selectedIds.has(e.raid.id));
  const allSelectedFeasible =
    selectedEntries.length > 0 && selectedEntries.every((e) => e.feasibility.feasible);
  const pendingDispatchIds = useMemo(
    () => new Set(pendingDispatchEntries.map((entry) => entry.raid.id)),
    [pendingDispatchEntries],
  );

  async function handleDispatch() {
    const dispatchEntries = selectedEntries;
    const ids = Array.from(selectedIds);
    const items = dispatchEntries.map((entry) => ({
      sagaId: entry.queueItem.sagaId,
      issueId: entry.queueItem.issueId,
      repo: entry.queueItem.repos[0] ?? '',
      workflowId: workflowOverride.get(entry.raid.id)?.id,
    }));
    setIsDispatching(true);
    setPendingDispatchEntries(dispatchEntries);
    setOptimisticQueued((prev) => new Set([...prev, ...ids]));
    setSelectedIds(new Set());

    try {
      const results = await dispatchBus.approve(items);
      await queueQuery.refetch();
      setOptimisticQueued(new Set());
      setPendingDispatchEntries([]);
      const spawned = results.filter((result) => result.status === 'spawned').length;
      toast({
        title:
          spawned === ids.length
            ? `Dispatched ${ids.length} raid${ids.length !== 1 ? 's' : ''}`
            : `Dispatched ${spawned} of ${ids.length} raids`,
        tone: spawned > 0 ? 'success' : 'critical',
      });
    } catch {
      setOptimisticQueued((prev) => {
        const next = new Set(prev);
        ids.forEach((id) => next.delete(id));
        return next;
      });
      setPendingDispatchEntries([]);
      toast({ title: 'Dispatch failed', tone: 'critical' });
    } finally {
      setIsDispatching(false);
    }
  }

  async function handlePauseToggle() {
    if (!dispatcherState) return;
    const nextRunning = !dispatcherState.running;
    setIsPausing(true);
    try {
      await dispatcherService.setRunning(nextRunning);
      await dispatcherQuery.refetch();
      toast({ title: nextRunning ? 'Dispatcher resumed' : 'Dispatcher paused' });
    } catch {
      toast({ title: 'Failed to update dispatcher', tone: 'critical' });
    } finally {
      setIsPausing(false);
    }
  }

  function handleApplyWorkflow(workflow: Workflow) {
    setWorkflowOverride((prev) => {
      const next = new Map(prev);
      selectedIds.forEach((id) => next.set(id, workflow));
      return next;
    });
    toast({
      title: `Applied "${workflow.name}" to ${selectedIds.size} raid${selectedIds.size !== 1 ? 's' : ''}`,
    });
  }

  function handleApplyThreshold(threshold: number) {
    setThresholdOverride(threshold);
    toast({ title: `Threshold → ${threshold.toFixed(2)}` });
  }

  function handleSaveRules(rules: RulesFormState) {
    setThresholdOverride(rules.threshold);
    setRulesOverride({
      maxConcurrentRaids: rules.maxConcurrentRaids,
      autoContinue: rules.autoContinue,
      retryCount: rules.retryCount,
    });
    toast({ title: 'Dispatch rules updated' });
  }

  function toggleId(id: string) {
    if (pendingDispatchIds.has(id)) return;

    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const isLoading = dispatcherQuery.isLoading || queueQuery.isLoading;
  const isError = dispatcherQuery.isError || queueQuery.isError;

  if (isLoading) {
    return (
      <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-p-6" role="status">
        <StateDot state="processing" pulse />
        <span className="niuu-text-sm niuu-text-text-secondary">loading dispatch queue…</span>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-p-6" role="alert">
        <StateDot state="failed" />
        <span className="niuu-text-sm niuu-text-text-secondary">failed to load dispatch queue</span>
      </div>
    );
  }

  return (
    <TooltipProvider>
      <div className="niuu-flex niuu-h-full niuu-overflow-hidden">
        {/* ── Left: queue ─────────────────────────────── */}
        <div className="niuu-flex niuu-flex-col niuu-flex-1 niuu-overflow-hidden">
          {/* Header */}
          <div className="niuu-px-4 niuu-py-3 niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary">
            <div className="niuu-text-xs niuu-uppercase niuu-tracking-wide niuu-text-text-muted niuu-mb-1">
              Dispatch queue
            </div>
            <div className="niuu-flex niuu-items-baseline niuu-justify-between">
              <h2 className="niuu-m-0 niuu-text-lg niuu-font-semibold niuu-text-text-primary">
                {counts.all} raids · {counts.ready} ready
              </h2>
              <div className="niuu-flex niuu-items-center niuu-gap-2">
                {dispatcherState && (
                  <>
                    <span className="niuu-text-xs niuu-font-mono niuu-bg-bg-elevated niuu-px-2 niuu-py-1 niuu-rounded niuu-text-text-secondary">
                      threshold <strong className="niuu-text-brand">{effectiveThreshold}%</strong>
                    </span>
                    <span className="niuu-text-xs niuu-font-mono niuu-bg-bg-elevated niuu-px-2 niuu-py-1 niuu-rounded niuu-text-text-secondary">
                      concurrent <strong>{effectiveMaxConcurrent}</strong>
                    </span>
                    <button
                      type="button"
                      onClick={handlePauseToggle}
                      disabled={isPausing}
                      className={cn(
                        'niuu-text-xs niuu-border niuu-border-border niuu-rounded-md niuu-px-2 niuu-py-1 niuu-transition-colors',
                        dispatcherState.running
                          ? 'niuu-text-text-secondary hover:niuu-text-text-primary niuu-bg-transparent'
                          : 'niuu-text-brand niuu-bg-bg-elevated',
                        isPausing && 'niuu-opacity-50 niuu-cursor-not-allowed',
                      )}
                      aria-label={
                        dispatcherState.running ? 'Pause dispatcher' : 'Resume dispatcher'
                      }
                    >
                      {isPausing
                        ? '…'
                        : dispatcherState.running
                          ? '⏸ Pause dispatcher'
                          : '▶ Resume dispatcher'}
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* Controls */}
          <div className="niuu-flex niuu-items-center niuu-gap-3 niuu-px-4 niuu-py-2 niuu-border-b niuu-border-border-subtle niuu-flex-wrap">
            <SegmentedFilter
              options={buildFilterOptions(counts)}
              value={statusFilter}
              onChange={(v) => {
                setStatusFilter(v);
                setSelectedIds(new Set());
              }}
              aria-label="Filter raids by status"
            />
            <input
              type="search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search raids…"
              aria-label="Search raids"
              className="niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-tertiary niuu-px-3 niuu-py-1.5 niuu-text-xs niuu-text-text-primary niuu-outline-none focus:niuu-border-brand niuu-ml-auto"
            />
          </div>

          {/* Inline batch dispatch bar */}
          <BatchDispatchBar
            selectedCount={selectedIds.size}
            canDispatch={allSelectedFeasible}
            onDispatch={handleDispatch}
            isDispatching={isDispatching}
            onApplyWorkflow={() => setShowWorkflowModal(true)}
            onOverrideThreshold={() => setShowThresholdModal(true)}
          />

          <PendingDispatchBar entries={pendingDispatchEntries} />

          {/* Grouped queue */}
          <div
            className="niuu-flex-1 niuu-overflow-y-auto niuu-p-2 niuu-flex niuu-flex-col niuu-gap-3"
            role="list"
            aria-label="Dispatch queue"
          >
            {groupedBySaga.length === 0 ? (
              <div className="niuu-py-12 niuu-text-center niuu-text-sm niuu-text-text-muted">
                No raids match the current filter.
              </div>
            ) : (
              groupedBySaga.map(([sagaId, group]) => (
                <div
                  key={sagaId}
                  role="listitem"
                  className="niuu-rounded-lg niuu-border niuu-border-border-subtle niuu-overflow-hidden niuu-bg-bg-secondary"
                >
                  <SagaGroupHeader
                    sagaName={group.sagaName}
                    trackerId={group.trackerId}
                    featureBranch={group.featureBranch}
                    raidCount={group.entries.length}
                    workflowName={group.workflowName}
                  />
                  <div className="niuu-p-2 niuu-flex niuu-flex-col niuu-gap-2">
                    {group.entries.map((entry) => (
                      <RaidRow
                        key={entry.raid.id}
                        entry={entry}
                        isSelected={selectedIds.has(entry.raid.id)}
                        onToggle={() => toggleId(entry.raid.id)}
                        workflowName={workflowOverride.get(entry.raid.id)?.name}
                        isPendingDispatch={pendingDispatchIds.has(entry.raid.id)}
                      />
                    ))}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* ── Right: rules panel ──────────────────────── */}
        <div
          className="niuu-border-l niuu-border-border-subtle niuu-overflow-y-auto niuu-bg-bg-primary niuu-w-[280px] niuu-shrink-0"
          aria-label="Dispatch rules panel"
        >
          {dispatcherState ? (
            <DispatchRulesPanel
              threshold={effectiveThreshold}
              maxConcurrentRaids={effectiveMaxConcurrent}
              autoContinue={effectiveAutoContinue}
              retryCount={effectiveRetryCount}
              onEdit={() => setShowEditRulesModal(true)}
            />
          ) : null}
        </div>
      </div>

      {/* Modals — workflow modal only mounts when open to avoid eager service calls */}
      {showWorkflowModal && (
        <WorkflowOverrideModal
          open={showWorkflowModal}
          onOpenChange={setShowWorkflowModal}
          selectedCount={selectedIds.size}
          onApply={handleApplyWorkflow}
        />
      )}
      <ThresholdOverrideModal
        open={showThresholdModal}
        onOpenChange={setShowThresholdModal}
        currentThreshold={effectiveThreshold / 100}
        onApply={(v) => handleApplyThreshold(Math.round(v * 100))}
      />
      <EditRulesModal
        open={showEditRulesModal}
        onOpenChange={setShowEditRulesModal}
        rules={{
          threshold: effectiveThreshold,
          maxConcurrentRaids: effectiveMaxConcurrent,
          autoContinue: effectiveAutoContinue,
          retryCount: effectiveRetryCount,
        }}
        onSave={handleSaveRules}
      />
    </TooltipProvider>
  );
}

// ---------------------------------------------------------------------------
// Main view (provides Toast context)
// ---------------------------------------------------------------------------

export function DispatchView() {
  return (
    <ToastProvider>
      <DispatchViewContent />
    </ToastProvider>
  );
}
