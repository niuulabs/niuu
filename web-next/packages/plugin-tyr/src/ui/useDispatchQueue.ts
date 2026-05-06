import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IDispatchBus, DispatchQueueItem } from '../ports';
import type { Raid, Phase, Saga, RaidStatus } from '../domain/saga';

export interface DispatchEntry {
  queueItem: DispatchQueueItem;
  raid: Raid;
  phase: Phase;
  saga: Saga;
  /** All phases for the saga — used by the upstream-blocked gate. */
  allPhases: Phase[];
}

function toRaidStatus(status: string): RaidStatus {
  switch (status) {
    case 'queued':
    case 'running':
    case 'review':
    case 'escalated':
    case 'merged':
    case 'failed':
      return status;
    default:
      return 'pending';
  }
}

function toDispatchEntry(item: DispatchQueueItem): DispatchEntry {
  const now = new Date().toISOString();
  const phaseId = `${item.sagaId}:${item.phaseName}`;

  const raid: Raid = {
    id: item.issueId,
    phaseId,
    trackerId: item.identifier,
    name: item.title,
    description: item.description,
    acceptanceCriteria: [],
    declaredFiles: [],
    estimateHours: item.estimate,
    status: toRaidStatus(item.status),
    confidence: 100,
    sessionId: null,
    reviewerSessionId: null,
    reviewRound: 0,
    branch: null,
    chronicleSummary: null,
    retryCount: 0,
    createdAt: now,
    updatedAt: now,
  };

  const phase: Phase = {
    id: phaseId,
    sagaId: item.sagaId,
    trackerId: item.phaseName,
    number: 1,
    name: item.phaseName,
    status: 'active',
    confidence: 100,
    raids: [raid],
  };

  const saga: Saga = {
    id: item.sagaId,
    trackerId: item.sagaSlug,
    trackerType: 'linear',
    slug: item.sagaSlug,
    name: item.sagaName,
    repos: item.repos,
    featureBranch: item.featureBranch,
    baseBranch: 'main',
    status: 'active',
    confidence: 100,
    createdAt: now,
    phaseSummary: { total: 1, completed: 0 },
    workflowId: item.workflowId,
    workflow: item.workflow,
    workflowVersion: item.workflowVersion,
  };

  return { queueItem: item, raid, phase, saga, allPhases: [phase] };
}

/** Returns the authoritative dispatcher queue from Tyr. */
export function useDispatchQueue() {
  const dispatch = useService<IDispatchBus>('tyr.dispatch');

  return useQuery({
    queryKey: ['tyr', 'dispatch-queue'],
    queryFn: async (): Promise<DispatchEntry[]> => {
      const items = await dispatch.getQueue();
      return items.map(toDispatchEntry);
    },
  });
}
