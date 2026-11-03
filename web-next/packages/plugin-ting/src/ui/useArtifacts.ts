import { useMemo } from 'react';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IRunLogService, IWorkflowService, ProjectSessions } from '../ports';
import {
  artifactsFromOutcomes,
  newestFirst,
  runRef,
  type Artifact,
  type RunOutcome,
  type RunRef,
} from '../domain/artifacts';
import { mergeWorkPages, useReviews, useWorkCollection } from './useWork';

/** A running run's log grows; it is read again on this cadence while it is on screen. */
const LIVE_RUN_REFRESH_MS = 15000;

export interface ArtifactProject {
  id: string;
  name: string;
}

export interface ArtifactRow extends Artifact {
  project: ArtifactProject | null;
}

function runIsSettled(run: RunRef): boolean {
  return run.condition === 'completed' || run.condition === 'failed';
}

function useWaitingSlugs(): ReadonlySet<string> {
  const reviews = useReviews();
  return useMemo(
    () =>
      new Set(
        (reviews.data?.items ?? [])
          .filter((item) => item.state === 'waiting')
          .map((item) => item.taskId),
      ),
    [reviews.data],
  );
}

function useProjectSessions() {
  const runLog = useService<IRunLogService>('ting.runLog');
  return useQuery({
    queryKey: ['ting', 'artifacts', 'project-sessions'],
    queryFn: () => runLog.projectSessions(),
  });
}

function projectBySession(projects: readonly ProjectSessions[] | undefined) {
  const map = new Map<string, ArtifactProject>();
  for (const project of projects ?? []) {
    for (const sessionId of project.sessionIds) {
      map.set(sessionId, { id: project.projectId, name: project.name });
    }
  }
  return map;
}

function outcomesQuery(runLog: IRunLogService, run: RunRef) {
  return {
    queryKey: ['ting', 'artifacts', 'outcomes', run.slug],
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      runLog.outcomes(run.sessionId, run.instanceId, signal),
    // A finished run's log no longer changes.
    staleTime: runIsSettled(run) ? Infinity : 0,
    refetchInterval: runIsSettled(run) ? (false as const) : LIVE_RUN_REFRESH_MS,
  };
}

/**
 * Every artifact across the runs Work knows, newest first, with the runs that could not be
 * read. Given a project, only the runs whose session is in it are read.
 */
export function useArtifacts(projectId?: string) {
  const runLog = useService<IRunLogService>('ting.runLog');
  const work = useWorkCollection();
  const waiting = useWaitingSlugs();
  const projects = useProjectSessions();
  const runs = useMemo(() => {
    const merged = mergeWorkPages(work.data?.pages);
    if (!merged) return [];
    if (projectId && !projects.data) return [];
    const inProject = projectId
      ? new Set(projects.data?.find((project) => project.projectId === projectId)?.sessionIds)
      : null;
    return [...merged.campaigns, ...merged.executions]
      .map(runRef)
      .filter((run): run is RunRef => run !== null)
      .filter((run) => !inProject || inProject.has(run.sessionId));
  }, [work.data, projects.data, projectId]);
  const outcomes = useQueries({ queries: runs.map((run) => outcomesQuery(runLog, run)) });
  const sessionProjects = useMemo(() => projectBySession(projects.data), [projects.data]);

  const rows = useMemo(() => {
    const all: ArtifactRow[] = [];
    runs.forEach((run, index) => {
      const data = outcomes[index]?.data;
      if (!data) return;
      for (const artifact of artifactsFromOutcomes(run, data, waiting)) {
        all.push({ ...artifact, project: sessionProjects.get(run.sessionId) ?? null });
      }
    });
    return newestFirst(all) as ArtifactRow[];
  }, [runs, outcomes, waiting, sessionProjects]);

  const unreadable = runs.flatMap((run, index) => {
    const error = outcomes[index]?.error;
    if (!error) return [];
    return [{ run, reason: error instanceof Error ? error.message : String(error) }];
  });
  const reading = outcomes.filter((query) => query.isPending).length;
  return {
    rows,
    loading: work.isPending || (Boolean(projectId) && projects.isPending),
    reading,
    runCount: runs.length,
    unreadable,
    error: work.error ?? projects.error,
  };
}

/** One run's outcomes, for its artifacts' review page. */
export function useRunOutcomes(run: RunRef | null) {
  const runLog = useService<IRunLogService>('ting.runLog');
  return useQuery({
    ...(run
      ? outcomesQuery(runLog, run)
      : { queryKey: ['ting', 'artifacts', 'outcomes', null], queryFn: async () => [] }),
    enabled: run !== null,
  }) as ReturnType<typeof useQuery<RunOutcome[]>>;
}

/** The run behind a slug, from the runs Work already lists. */
export function useRun(slug: string): { run: RunRef | null; loading: boolean; error: unknown } {
  const work = useWorkCollection();
  const run = useMemo(() => {
    const merged = mergeWorkPages(work.data?.pages);
    const item = [...(merged?.campaigns ?? []), ...(merged?.executions ?? [])].find(
      (summary) => summary.campaignSlug === slug,
    );
    return item ? runRef(item) : null;
  }, [work.data, slug]);
  return { run, loading: work.isPending, error: work.error };
}

export function useWaiting(): ReadonlySet<string> {
  return useWaitingSlugs();
}

export function useSendToRun(run: RunRef | null) {
  const runLog = useService<IRunLogService>('ting.runLog');
  return useMutation({
    mutationFn: (content: string) => {
      if (!run) throw new Error('This run is not loaded.');
      return runLog.sendMessage(run.sessionId, run.instanceId, content);
    },
  });
}

export function useStartRevision() {
  const workflows = useService<IWorkflowService>('ting.workflows');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      workflowId,
      prompt,
      repo,
      branch,
      connectionId,
      workflowVersion,
    }: {
      workflowId: string;
      prompt: string;
      repo?: string;
      branch?: string;
      connectionId?: string;
      workflowVersion?: string;
    }) =>
      workflows.launchWorkflow(workflowId, {
        prompt,
        ...(repo ? { repo } : {}),
        ...(branch ? { branch } : {}),
        ...(connectionId ? { connectionId } : {}),
        ...(workflowVersion ? { workflowVersion } : {}),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['ting', 'work'] }),
  });
}
