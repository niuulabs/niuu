import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ValkyrieDashboard, ValkyrieSignalEvent } from '../domain';
import type {
  AutonomyUpdateRequest,
  HuddleSendRequest,
  IValkyrieService,
  IValkyrieSignalStream,
  LearningDecisionRequest,
} from '../ports';

export const VALKYRIE_DASHBOARD_QUERY_KEY = ['valkyrie', 'dashboard'] as const;

export function useValkyrieDashboard() {
  const service = useService<IValkyrieService>('valkyrie');
  return useQuery({
    queryKey: VALKYRIE_DASHBOARD_QUERY_KEY,
    queryFn: () => service.getDashboard(),
    refetchInterval: 10_000,
  });
}

export function useValkyrieSignals(maxEvents = 80): ValkyrieSignalEvent[] {
  const stream = useService<IValkyrieSignalStream>('valkyrie.signals');
  const [events, setEvents] = useState<ValkyrieSignalEvent[]>([]);

  useEffect(() => {
    return stream.subscribe((event) => {
      setEvents((prev) => {
        if (prev.some((entry) => entry.id === event.id)) return prev;
        const next = [...prev, event];
        return next.length > maxEvents ? next.slice(next.length - maxEvents) : next;
      });
    });
  }, [maxEvents, stream]);

  return events;
}

function replaceDashboardLearning(
  dashboard: ValkyrieDashboard | undefined,
  learning: ValkyrieDashboard['learnings'][number],
): ValkyrieDashboard | undefined {
  if (!dashboard) return dashboard;
  return {
    ...dashboard,
    learnings: dashboard.learnings.map((entry) => (entry.id === learning.id ? learning : entry)),
  };
}

export function useValkyrieActions() {
  const service = useService<IValkyrieService>('valkyrie');
  const queryClient = useQueryClient();

  const adoptLearning = useMutation({
    mutationFn: (request: LearningDecisionRequest) => service.adoptLearning(request),
    onSuccess: (learning) => {
      queryClient.setQueryData<ValkyrieDashboard>(VALKYRIE_DASHBOARD_QUERY_KEY, (dashboard) =>
        replaceDashboardLearning(dashboard, learning),
      );
    },
  });

  const rejectLearning = useMutation({
    mutationFn: (request: LearningDecisionRequest) => service.rejectLearning(request),
    onSuccess: (learning) => {
      queryClient.setQueryData<ValkyrieDashboard>(VALKYRIE_DASHBOARD_QUERY_KEY, (dashboard) =>
        replaceDashboardLearning(dashboard, learning),
      );
    },
  });

  const overrideLearning = useMutation({
    mutationFn: (request: LearningDecisionRequest) => service.overrideLearning(request),
    onSuccess: (learning) => {
      queryClient.setQueryData<ValkyrieDashboard>(VALKYRIE_DASHBOARD_QUERY_KEY, (dashboard) =>
        replaceDashboardLearning(dashboard, learning),
      );
    },
  });

  const canaryLearning = useMutation({
    mutationFn: (request: LearningDecisionRequest) => service.canaryLearning(request),
    onSuccess: (learning) => {
      queryClient.setQueryData<ValkyrieDashboard>(VALKYRIE_DASHBOARD_QUERY_KEY, (dashboard) =>
        replaceDashboardLearning(dashboard, learning),
      );
    },
  });

  const promoteLearning = useMutation({
    mutationFn: (request: LearningDecisionRequest) => service.promoteLearning(request),
    onSuccess: (learning) => {
      queryClient.setQueryData<ValkyrieDashboard>(VALKYRIE_DASHBOARD_QUERY_KEY, (dashboard) =>
        replaceDashboardLearning(dashboard, learning),
      );
    },
  });

  const demoteLearning = useMutation({
    mutationFn: (request: LearningDecisionRequest) => service.demoteLearning(request),
    onSuccess: (learning) => {
      queryClient.setQueryData<ValkyrieDashboard>(VALKYRIE_DASHBOARD_QUERY_KEY, (dashboard) =>
        replaceDashboardLearning(dashboard, learning),
      );
    },
  });

  const rollbackLearning = useMutation({
    mutationFn: (request: LearningDecisionRequest) => service.rollbackLearning(request),
    onSuccess: (learning) => {
      queryClient.setQueryData<ValkyrieDashboard>(VALKYRIE_DASHBOARD_QUERY_KEY, (dashboard) =>
        replaceDashboardLearning(dashboard, learning),
      );
    },
  });

  const joinHuddle = useMutation({
    mutationFn: (huddleId: string) => service.joinHuddle(huddleId),
    onSuccess: (huddle) => {
      queryClient.setQueryData<ValkyrieDashboard>(VALKYRIE_DASHBOARD_QUERY_KEY, (dashboard) =>
        dashboard
          ? {
              ...dashboard,
              huddles: dashboard.huddles.map((entry) => (entry.id === huddle.id ? huddle : entry)),
            }
          : dashboard,
      );
    },
  });

  const leaveHuddle = useMutation({
    mutationFn: (huddleId: string) => service.leaveHuddle(huddleId),
    onSuccess: (huddle) => {
      queryClient.setQueryData<ValkyrieDashboard>(VALKYRIE_DASHBOARD_QUERY_KEY, (dashboard) =>
        dashboard
          ? {
              ...dashboard,
              huddles: dashboard.huddles.map((entry) => (entry.id === huddle.id ? huddle : entry)),
            }
          : dashboard,
      );
    },
  });

  const sendHuddleMessage = useMutation({
    mutationFn: (request: HuddleSendRequest) => service.sendHuddleMessage(request),
    onSuccess: (message) => {
      queryClient.setQueryData<ValkyrieDashboard>(VALKYRIE_DASHBOARD_QUERY_KEY, (dashboard) =>
        dashboard
          ? {
              ...dashboard,
              huddles: dashboard.huddles.map((entry) =>
                entry.id === message.huddleId
                  ? {
                      ...entry,
                      messages: entry.messages.some((existing) => existing.id === message.id)
                        ? entry.messages
                        : [...entry.messages, message],
                      lastActivityAt: message.createdAt,
                    }
                  : entry,
              ),
            }
          : dashboard,
      );
    },
  });

  const updateAutonomy = useMutation({
    mutationFn: (request: AutonomyUpdateRequest) => service.updateAutonomy(request),
    onSuccess: (dashboard) => {
      queryClient.setQueryData(VALKYRIE_DASHBOARD_QUERY_KEY, dashboard);
    },
  });

  return useMemo(
    () => ({
      adoptLearning,
      canaryLearning,
      rejectLearning,
      overrideLearning,
      promoteLearning,
      demoteLearning,
      rollbackLearning,
      joinHuddle,
      leaveHuddle,
      sendHuddleMessage,
      updateAutonomy,
    }),
    [
      adoptLearning,
      canaryLearning,
      demoteLearning,
      joinHuddle,
      leaveHuddle,
      overrideLearning,
      promoteLearning,
      rejectLearning,
      rollbackLearning,
      sendHuddleMessage,
      updateAutonomy,
    ],
  );
}
