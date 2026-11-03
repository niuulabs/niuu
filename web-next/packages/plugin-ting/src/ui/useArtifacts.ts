import { useMemo } from 'react';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IRunLogService, IWorkflowService, ProjectSessions } from '../ports';
import {
  artifactsFromPublications,
  isReadable,
  newestFirst,
  runRef,
  type Artifact,
  type ArtifactVersion,
  type Publication,
  type RunRef,
} from '../domain/artifacts';
import { mergeWorkPages, useReviews, useWorkCollection } from './useWork';

/** A running run may present again; what it presented is read again on this cadence. */
const LIVE_REFRESH_MS = 15000;

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

/** The hosts runs live on; `null` is the host this UI talks to. */
function hostsOf(runs: readonly RunRef[]): (string | null)[] {
  return [...new Set(runs.map((run) => run.instanceId))];
}

/**
 * Every artifact across the runs Work knows, newest first, with the hosts that could not
 * be read. Given a project, only the runs whose session is in it are listed.
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
  const hosts = useMemo(() => hostsOf(runs), [runs]);
  const live = runs.some((run) => !runIsSettled(run));
  const feeds = useQueries({
    queries: hosts.map((host) => ({
      queryKey: ['ting', 'artifacts', 'publications', host],
      queryFn: ({ signal }: { signal: AbortSignal }) => runLog.publications(host, { signal }),
      refetchInterval: live ? LIVE_REFRESH_MS : (false as const),
    })),
  });
  const sessionProjects = useMemo(() => projectBySession(projects.data), [projects.data]);

  const rows = useMemo(() => {
    const byHost = new Map<string | null, Publication[]>();
    hosts.forEach((host, index) => {
      const data = feeds[index]?.data;
      if (data) byHost.set(host, data);
    });
    const all: ArtifactRow[] = [];
    for (const run of runs) {
      const published = byHost.get(run.instanceId);
      if (!published) continue;
      for (const artifact of artifactsFromPublications(run, published, waiting)) {
        all.push({ ...artifact, project: sessionProjects.get(run.sessionId) ?? null });
      }
    }
    return newestFirst(all);
  }, [runs, hosts, feeds, waiting, sessionProjects]);

  const unreadable = hosts.flatMap((host, index) => {
    const error = feeds[index]?.error;
    if (!error) return [];
    return [{ host, reason: error instanceof Error ? error.message : String(error) }];
  });
  return {
    rows,
    loading:
      work.isPending ||
      (Boolean(projectId) && projects.isPending) ||
      feeds.some((query) => query.isPending),
    unreadable,
    error: work.error ?? projects.error,
  };
}

/** What one run presented, for its review page. */
export function useRunPublications(run: RunRef | null) {
  const runLog = useService<IRunLogService>('ting.runLog');
  return useQuery({
    queryKey: ['ting', 'artifacts', 'run-publications', run?.slug ?? null],
    queryFn: ({ signal }) =>
      runLog.publications(run!.instanceId, { sessionId: run!.sessionId, signal }),
    enabled: run !== null,
    refetchInterval: run && !runIsSettled(run) ? LIVE_REFRESH_MS : false,
  });
}

/**
 * The text of every readable version, by revision id. A presented file never changes
 * under its id, so each is read once.
 */
export function useArtifactTexts(run: RunRef | null, artifacts: readonly Artifact[]) {
  const runLog = useService<IRunLogService>('ting.runLog');
  const versions: ArtifactVersion[] = artifacts
    .flatMap((artifact) => artifact.versions)
    .filter(isReadable);
  const texts = useQueries({
    queries: versions.map((version) => ({
      queryKey: ['ting', 'artifacts', 'text', run?.sessionId ?? null, version.revisionId],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        runLog.presentedText(run!.sessionId, run!.instanceId, version.revisionId, signal),
      enabled: run !== null,
      staleTime: Infinity,
    })),
  });
  const map = new Map<string, string>();
  versions.forEach((version, index) => {
    const data = texts[index]?.data;
    if (typeof data === 'string') map.set(version.revisionId, data);
  });
  const failed = versions.flatMap((version, index) => {
    const error = texts[index]?.error;
    return error
      ? [{ version, reason: error instanceof Error ? error.message : String(error) }]
      : [];
  });
  return { texts: map, failed, reading: texts.some((query) => query.isPending) };
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
