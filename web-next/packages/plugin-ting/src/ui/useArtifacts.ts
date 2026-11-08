import { useMemo } from 'react';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IRunLogService, IWorkService, IWorkflowService, ProjectSessions } from '../ports';
import type { ReviewRunSession } from '../domain/review';
import {
  artifactsFromPublications,
  isReadable,
  newestFirst,
  runRef,
  runWaits,
  runWithSessions,
  sessionArtifacts,
  type Artifact,
  type ArtifactVersion,
  type Publication,
  type RunRef,
  type RunSession,
  type SessionRef,
  RUN_ENDED,
} from '../domain/artifacts';
import { mergeWorkPages, useReviews, useWorkCollection } from './useWork';

/** A running session may present again; what was presented is read again on this cadence. */
const LIVE_REFRESH_MS = 15000;

export interface ArtifactProject {
  id: string;
  name: string;
}

export interface ArtifactRow extends Artifact {
  project: ArtifactProject | null;
}

/** A run held at a gate that has presented nothing yet: someone still has to answer it. */
export interface WaitingRun {
  run: RunRef;
  project: ArtifactProject | null;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function runIsSettled(run: RunRef): boolean {
  return RUN_ENDED.has(run.condition);
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

/** A session that is no workflow run, on the host that holds its files. */
interface PresentingSession {
  sessionId: string;
  host: string | null;
}

/** How a session Forge no longer has is named: its files outlive it. */
function goneSession(sessionId: string, host: string | null): SessionRef {
  return {
    sessionId,
    instanceId: host,
    title: `Session ${sessionId.slice(0, 8)} (gone)`,
    running: false,
  };
}

/**
 * Every artifact sessions presented, newest first, with what could not be read: the
 * runs Work knows, and every other session on every Forge host. Given a project, only
 * its own sessions are listed.
 */
export function useArtifacts(projectId?: string) {
  const runLog = useService<IRunLogService>('ting.runLog');
  const work = useWorkCollection();
  const waiting = useWaitingSlugs();
  const projects = useProjectSessions();
  const registry = useQuery({
    queryKey: ['ting', 'artifacts', 'hosts'],
    queryFn: () => runLog.hosts(),
  });
  const allRuns = useMemo(() => {
    const merged = mergeWorkPages(work.data?.pages);
    if (!merged) return [];
    return [...merged.campaigns, ...merged.executions]
      .map(runRef)
      .filter((run): run is RunRef => run !== null);
  }, [work.data]);
  const inProject = useMemo(() => {
    if (!projectId) return null;
    return new Set(
      projects.data?.find((project) => project.projectId === projectId)?.sessionIds ?? [],
    );
  }, [projectId, projects.data]);
  const ready = !projectId || Boolean(projects.data);
  const runs = useMemo(
    () => (ready ? allRuns.filter((run) => !inProject || inProject.has(run.sessionId)) : []),
    [allRuns, inProject, ready],
  );
  const hosts = useMemo(
    () => [...new Set([...(registry.data ?? []), ...runs.map((run) => run.instanceId)])],
    [registry.data, runs],
  );
  const feeds = useQueries({
    queries: hosts.map((host) => ({
      queryKey: ['ting', 'artifacts', 'publications', host],
      queryFn: ({ signal }: { signal: AbortSignal }) => runLog.publications(host, { signal }),
      refetchInterval: LIVE_REFRESH_MS,
    })),
  });
  const byHost = useMemo(() => {
    const map = new Map<string | null, Publication[]>();
    hosts.forEach((host, index) => {
      const data = feeds[index]?.data;
      if (data) map.set(host, data);
    });
    return map;
  }, [hosts, feeds]);

  // Sessions that presented files and are no run Work lists.
  const others = useMemo((): PresentingSession[] => {
    if (!ready) return [];
    const runSessions = new Set(allRuns.map((run) => run.sessionId));
    const seen = new Map<string, PresentingSession>();
    for (const [host, published] of byHost) {
      for (const publication of published) {
        const id = publication.sessionId;
        if (runSessions.has(id) || (inProject && !inProject.has(id))) continue;
        seen.set(`${host}|${id}`, { sessionId: id, host });
      }
    }
    return [...seen.values()];
  }, [allRuns, byHost, inProject, ready]);
  const sessions = useQueries({
    queries: others.map((item) => ({
      queryKey: ['ting', 'artifacts', 'session', item.host, item.sessionId],
      queryFn: () => runLog.session(item.sessionId, item.host),
    })),
  });
  const sessionProjects = useMemo(() => projectBySession(projects.data), [projects.data]);

  const rows = useMemo(() => {
    const all: ArtifactRow[] = [];
    for (const run of runs) {
      const published = byHost.get(run.instanceId);
      if (!published) continue;
      for (const artifact of artifactsFromPublications(run, published, waiting)) {
        all.push({ ...artifact, project: sessionProjects.get(run.sessionId) ?? null });
      }
    }
    others.forEach((item, index) => {
      const lookup = sessions[index];
      if (!lookup || lookup.data === undefined) return;
      const session = lookup.data ?? goneSession(item.sessionId, item.host);
      for (const artifact of sessionArtifacts(session, byHost.get(item.host) ?? [])) {
        all.push({ ...artifact, project: sessionProjects.get(item.sessionId) ?? null });
      }
    });
    return newestFirst(all);
  }, [runs, byHost, waiting, sessionProjects, others, sessions]);

  const waitingRuns = useMemo((): WaitingRun[] => {
    const presented = new Set(rows.flatMap((row) => (row.run ? [row.run.slug] : [])));
    return runs
      .filter((run) => runWaits(run, waiting) && !presented.has(run.slug))
      .map((run) => ({ run, project: sessionProjects.get(run.sessionId) ?? null }));
  }, [runs, rows, waiting, sessionProjects]);

  const unreadable = [
    ...hosts.flatMap((host, index) => {
      const error = feeds[index]?.error;
      return error ? [{ label: host ?? 'This host', reason: message(error) }] : [];
    }),
    ...others.flatMap((item, index) => {
      const error = sessions[index]?.error;
      return error ? [{ label: `Session ${item.sessionId}`, reason: message(error) }] : [];
    }),
  ];
  return {
    rows,
    waitingRuns,
    loading:
      work.isPending ||
      registry.isPending ||
      (Boolean(projectId) && projects.isPending) ||
      feeds.some((query) => query.isPending) ||
      sessions.some((query) => query.isPending),
    unreadable,
    error: work.error ?? projects.error ?? registry.error,
  };
}

/** Where a session's files are read from. */
type SessionAt = Pick<SessionRef, 'sessionId' | 'instanceId'>;

/** What one run presented, in every session it worked in, for its review page. */
export function useRunPublications(run: RunRef | null) {
  const runLog = useService<IRunLogService>('ting.runLog');
  const live = run !== null && !runIsSettled(run);
  const sessions = run?.sessions ?? [];
  const reads = useQueries({
    queries: sessions.map((session) => ({
      queryKey: [
        'ting',
        'artifacts',
        'session-publications',
        session.instanceId,
        session.sessionId,
      ],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        runLog.publications(session.instanceId, { sessionId: session.sessionId, signal }),
      refetchInterval: live ? LIVE_REFRESH_MS : (false as const),
    })),
  });
  const data = useMemo(
    () =>
      reads.every((read) => read.data !== undefined)
        ? reads.flatMap((read) => read.data ?? [])
        : undefined,
    [reads],
  );
  return {
    data,
    isLoading: reads.some((read) => read.isPending),
    error: reads.find((read) => read.error)?.error ?? null,
  };
}

/** What one session presented, for its review page; re-read while it may present more. */
export function useSessionPublications(session: SessionAt | null, live: boolean) {
  const runLog = useService<IRunLogService>('ting.runLog');
  return useQuery({
    queryKey: [
      'ting',
      'artifacts',
      'session-publications',
      session?.instanceId ?? null,
      session?.sessionId ?? null,
    ],
    queryFn: ({ signal }) =>
      runLog.publications(session!.instanceId, { sessionId: session!.sessionId, signal }),
    enabled: session !== null,
    refetchInterval: live ? LIVE_REFRESH_MS : false,
  });
}

/** A session that presented files: `null` once Forge no longer has it. */
export function useArtifactSession(sessionId: string, instanceId: string | null) {
  const runLog = useService<IRunLogService>('ting.runLog');
  return useQuery({
    queryKey: ['ting', 'artifacts', 'session', instanceId, sessionId],
    queryFn: async () =>
      (await runLog.session(sessionId, instanceId)) ?? goneSession(sessionId, instanceId),
  });
}

/**
 * The text of every readable version, by revision id, read from the session that
 * presented it. A presented file never changes under its id, so each is read once.
 */
export function useArtifactTexts(artifacts: readonly Artifact[]) {
  const runLog = useService<IRunLogService>('ting.runLog');
  const versions: { version: ArtifactVersion; session: SessionAt }[] = artifacts.flatMap(
    (artifact) =>
      artifact.versions
        .filter(isReadable)
        .map((version) => ({ version, session: artifact.session })),
  );
  const texts = useQueries({
    queries: versions.map(({ version, session }) => ({
      queryKey: ['ting', 'artifacts', 'text', session.sessionId, version.revisionId],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        runLog.presentedText(session.sessionId, session.instanceId, version.fileId, signal),
      staleTime: Infinity,
    })),
  });
  const map = new Map<string, string>();
  versions.forEach(({ version }, index) => {
    const data = texts[index]?.data;
    if (typeof data === 'string') map.set(version.revisionId, data);
  });
  const failed = versions.flatMap(({ version }, index) => {
    const error = texts[index]?.error;
    return error
      ? [{ version, reason: error instanceof Error ? error.message : String(error) }]
      : [];
  });
  return { texts: map, failed, reading: texts.some((query) => query.isPending) };
}

/**
 * The run behind a slug, read by its slug whatever page of Work it is on, with the
 * sessions its review lists.
 */
export function useRun(
  slug: string,
  sessions: readonly ReviewRunSession[] | undefined,
): { run: RunRef | null; loading: boolean; error: unknown } {
  const work = useService<IWorkService>('ting.work');
  const detail = useQuery({
    queryKey: ['ting', 'work', 'run', slug],
    queryFn: () => work.run(slug),
  });
  const run = useMemo(() => {
    if (!detail.data) return null;
    const listed: RunSession[] = (sessions ?? []).map((session) => ({
      sessionId: session.sessionId,
      instanceId: session.connectionId,
      member: session.member,
    }));
    return runWithSessions(detail.data.item, listed);
  }, [detail.data, sessions]);
  return { run, loading: detail.isPending, error: detail.error };
}

export function useWaiting(): ReadonlySet<string> {
  return useWaitingSlugs();
}

/** Notes on an artifact, sent as a chat message to the session that presented it. */
export function useSendToRun() {
  const runLog = useService<IRunLogService>('ting.runLog');
  return useMutation({
    mutationFn: ({ session, content }: { session: SessionAt; content: string }) =>
      runLog.sendMessage(session.sessionId, session.instanceId, content),
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
