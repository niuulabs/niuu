import type { IWorkService } from '../ports';

/** A run's gate waiting for a person, as a host that shows the run asks for it. */
export interface WaitingRunGate {
  gateId: string;
  activationId: string;
  title: string;
  summary: string;
  requestedAt: string;
}

/** A run's step that nothing can take up while the run's sessions are stopped. */
export interface PausedRunStep {
  nodeId: string;
  title: string;
}

/** A run's waiting gate read and answered through Ting's run reviews and gate decision. */
export interface RunGateService {
  waitingGate(runId: string): Promise<WaitingRunGate | null>;
  pausedStep(runId: string): Promise<PausedRunStep | null>;
  decide(
    runId: string,
    gate: Pick<WaitingRunGate, 'gateId' | 'activationId'>,
    decision: 'approve' | 'request_changes',
    notes: string,
  ): Promise<void>;
}

/**
 * The run gates a host shows (Forge, above a run's coordinator's composer): the newest
 * pending revision of the run's reviews, decided as the review page decides it, and the step
 * the run's stopped sessions hold (a ``paused`` node of the run's graph).
 */
export function buildRunGateService(work: IWorkService): RunGateService {
  return {
    async waitingGate(runId) {
      const { reviews } = await work.runReviews(runId);
      for (const review of reviews) {
        const latest = review.revisions[review.revisions.length - 1];
        if (latest?.status !== 'pending') continue;
        return {
          gateId: latest.gateId,
          activationId: latest.activationId,
          title: review.title,
          summary: latest.summary,
          requestedAt: latest.requestedAt,
        };
      }
      return null;
    },
    async pausedStep(runId) {
      const graph = await work.runGraph(runId);
      for (const node of graph.nodes) {
        if (graph.states.get(node.id)?.state !== 'paused') continue;
        return { nodeId: node.id, title: node.label };
      }
      return null;
    },
    decide(runId, gate, decision, notes) {
      return work.decideRunGate(runId, {
        gateId: gate.gateId,
        activationId: gate.activationId,
        decision,
        notes,
      });
    },
  };
}
