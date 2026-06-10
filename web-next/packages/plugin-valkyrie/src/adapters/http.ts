import { openEventStream, type ApiClient, type EventStreamHandle } from '@niuulabs/query';
import { normalizeValkyrieSignalEvent, type ValkyrieDashboard } from '../domain';
import type {
  HuddleSendRequest,
  HuddleJoinRequest,
  IValkyrieService,
  IValkyrieSignalStream,
  LearningDecisionRequest,
  ValkyrieSignalListener,
  AutonomyUpdateRequest,
} from '../ports';

export function buildValkyrieHttpAdapter(client: ApiClient): IValkyrieService {
  return {
    getDashboard() {
      return client.get<ValkyrieDashboard>('/dashboard');
    },
    listEnvironments() {
      return client.get<ValkyrieDashboard['environments']>('/environments');
    },
    getEnvironment(environmentId) {
      return client.get<ValkyrieDashboard['environments'][number] | null>(
        `/environments/${encodeURIComponent(environmentId)}`,
      );
    },
    listFlocks() {
      return client.get<ValkyrieDashboard['flocks']>('/flocks');
    },
    getFlock(flockId) {
      return client.get<ValkyrieDashboard['flocks'][number] | null>(
        `/flocks/${encodeURIComponent(flockId)}`,
      );
    },
    getLearning(learningId) {
      return client.get<ValkyrieDashboard['learnings'][number]>(
        `/learnings/${encodeURIComponent(learningId)}`,
      );
    },
    joinHuddle(request: HuddleJoinRequest) {
      return client.post<ValkyrieDashboard['huddles'][number]>(
        `/huddles/${encodeURIComponent(request.huddleId)}/join`,
        request,
      );
    },
    sendHuddleMessage(request: HuddleSendRequest) {
      return client.post<ValkyrieDashboard['huddles'][number]['messages'][number]>(
        `/huddles/${encodeURIComponent(request.huddleId)}/messages`,
        request,
      );
    },
    leaveHuddle(huddleId) {
      return client.post<ValkyrieDashboard['huddles'][number]>(
        `/huddles/${encodeURIComponent(huddleId)}/leave`,
        {},
      );
    },
    adoptLearning(request: LearningDecisionRequest) {
      return client.post<ValkyrieDashboard['learnings'][number]>(
        `/learnings/${encodeURIComponent(request.learningId)}/adopt`,
        request,
      );
    },
    rejectLearning(request: LearningDecisionRequest) {
      return client.post<ValkyrieDashboard['learnings'][number]>(
        `/learnings/${encodeURIComponent(request.learningId)}/reject`,
        request,
      );
    },
    overrideLearning(request: LearningDecisionRequest) {
      return client.post<ValkyrieDashboard['learnings'][number]>(
        `/learnings/${encodeURIComponent(request.learningId)}/override`,
        request,
      );
    },
    canaryLearning(request: LearningDecisionRequest) {
      return client.post<ValkyrieDashboard['learnings'][number]>(
        `/learnings/${encodeURIComponent(request.learningId)}/canary`,
        request,
      );
    },
    promoteLearning(request: LearningDecisionRequest) {
      return client.post<ValkyrieDashboard['learnings'][number]>(
        `/learnings/${encodeURIComponent(request.learningId)}/promote`,
        request,
      );
    },
    demoteLearning(request: LearningDecisionRequest) {
      return client.post<ValkyrieDashboard['learnings'][number]>(
        `/learnings/${encodeURIComponent(request.learningId)}/demote`,
        request,
      );
    },
    rollbackLearning(request: LearningDecisionRequest) {
      return client.post<ValkyrieDashboard['learnings'][number]>(
        `/learnings/${encodeURIComponent(request.learningId)}/rollback`,
        request,
      );
    },
    replaySignal(request) {
      return client.post('/proof/replay-signal', request);
    },
    updateAutonomy(request: AutonomyUpdateRequest) {
      return client.post<ValkyrieDashboard>('/autonomy', request);
    },
  };
}

export function buildValkyrieSignalSseStream(url: string): IValkyrieSignalStream {
  const listeners = new Set<ValkyrieSignalListener>();
  let handle: EventStreamHandle | null = null;

  function ensureOpen(): void {
    if (handle) return;
    handle = openEventStream(url, {
      onMessage: (raw) => {
        try {
          const event = normalizeValkyrieSignalEvent(JSON.parse(raw));
          if (!event) return;
          for (const listener of listeners) listener(event);
        } catch {
          // Malformed frame — drop it and wait for the next event.
        }
      },
    });
  }

  function maybeClose(): void {
    if (listeners.size === 0 && handle) {
      handle.close();
      handle = null;
    }
  }

  return {
    subscribe(listener) {
      listeners.add(listener);
      ensureOpen();
      return () => {
        listeners.delete(listener);
        maybeClose();
      };
    },
  };
}
