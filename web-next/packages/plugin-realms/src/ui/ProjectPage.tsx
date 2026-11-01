import { useState } from 'react';
import { Link, useParams } from '@tanstack/react-router';
import { SectionCard, type ForgeProject } from '@niuulabs/plugin-volundr';
import { EmptyState, ErrorState, LoadingState, ProjectIcon } from '@niuulabs/ui';
import {
  orderCards,
  ticketsOf,
  useRealmsHome,
  useVolundrSessions,
} from '../application/useRealmsHome';
import { useForgeProjects, useUpdateProject } from '../application/useProjects';
import { NeedsYouRow } from './NeedsYouRow';
import { RealmCard } from './RealmCard';
import { SessionsTable } from './SessionsTable';
import { NeedsYouError, TicketReviewRow } from './TicketReviewRow';

const BUTTON =
  'niuu:self-start niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-text-primary niuu:disabled:opacity-40';
const PRIMARY =
  'niuu:rounded-md niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-brand-300';
const CONTROL =
  'niuu:w-full niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-2.5 niuu:py-1.5 niuu:text-sm niuu:text-text-primary';

/** What every workspace in the project shares. */
function SharedSettings({ project }: { project: ForgeProject }) {
  const update = useUpdateProject();
  const [brief, setBrief] = useState(project.brief ?? '');
  const [repoUrl, setRepoUrl] = useState(project.repo_url ?? '');
  const changed = brief !== (project.brief ?? '') || repoUrl !== (project.repo_url ?? '');
  return (
    <SectionCard
      title="Shared by all of them"
      description="Every session in this project reads the brief."
    >
      <div className="niuu:flex niuu:flex-col niuu:gap-3" data-testid="project-shared">
        <label className="niuu:flex niuu:flex-col niuu:gap-1 niuu:text-xs niuu:text-text-muted">
          Brief
          <textarea
            aria-label="Project brief"
            rows={4}
            className={CONTROL}
            value={brief}
            onChange={(event) => setBrief(event.target.value)}
          />
        </label>
        <label className="niuu:flex niuu:flex-col niuu:gap-1 niuu:text-xs niuu:text-text-muted">
          Repository
          <input
            aria-label="Project repository"
            className={CONTROL}
            placeholder="None. Only work that changes code needs one."
            value={repoUrl}
            onChange={(event) => setRepoUrl(event.target.value)}
          />
        </label>
        <button
          type="button"
          className={BUTTON}
          disabled={!changed || update.isPending || project.revision === undefined}
          onClick={() => update.mutate({ project, changes: { brief, repo_url: repoUrl } })}
        >
          {update.isPending ? 'Saving…' : 'Save'}
        </button>
        {update.error ? (
          <span className="niuu:text-xs niuu:text-critical-fg">{String(update.error)}</span>
        ) : null}
      </div>
    </SectionCard>
  );
}

/** A project: the workspaces that serve it and what they share. */
export function ProjectPage() {
  const { projectId } = useParams({ strict: false }) as { projectId: string };
  const projects = useForgeProjects();
  const home = useRealmsHome();
  const sessions = useVolundrSessions();
  const project = projects.data?.find((entry) => entry.id === projectId) ?? null;

  if (home.isLoading || projects.isLoading) return <LoadingState label="Loading project…" />;
  if (home.error ?? projects.error)
    return (
      <ErrorState
        title="Could not load the project"
        message={String(home.error ?? projects.error)}
      />
    );

  const cards = orderCards(home.cards.filter((card) => card.realm.project_id === projectId));
  if (!project && cards.length === 0) {
    return (
      <ErrorState
        title="No such project"
        message="This project does not exist, or no Forge you can reach holds it."
        action={
          <Link to="/realms" className={BUTTON}>
            Back to workspaces
          </Link>
        }
      />
    );
  }
  const environments = new Set(cards.map((card) => card.resident?.environmentId));
  const needsYou = home.pendingReviews.filter((item) => environments.has(item.environmentId));
  const tickets = ticketsOf(cards);
  const waiting = needsYou.length + tickets.length;
  const projectSessions = (sessions.data ?? []).filter(
    (session) => session.coordination?.projectId === projectId,
  );

  return (
    <div
      className="niuu:flex niuu:h-full niuu:flex-col niuu:gap-5 niuu:overflow-auto niuu:px-10 niuu:py-7"
      data-testid="project-page"
    >
      <header className="niuu:flex niuu:items-end niuu:justify-between niuu:gap-6">
        <div className="niuu:flex niuu:flex-col niuu:gap-1">
          <Link to="/realms" className="niuu:text-xs niuu:text-brand-300">
            Workspaces
          </Link>
          <h1 className="niuu:m-0 niuu:flex niuu:items-center niuu:gap-2 niuu:text-2xl niuu:font-bold niuu:tracking-tight niuu:text-text-primary">
            <ProjectIcon size={20} className="niuu:text-brand-300" />
            {project?.name ?? 'Project'}
          </h1>
          {project?.brief ? (
            <p className="niuu:m-0 niuu:text-[15px] niuu:text-text-secondary">{project.brief}</p>
          ) : null}
        </div>
        <Link to="/realms/new" search={{ project: projectId } as never} className={PRIMARY}>
          New workspace
        </Link>
      </header>

      {cards.length === 0 ? (
        <EmptyState
          title="No workspaces in this project yet"
          description="A workspace is one resident and the job you give it."
        />
      ) : (
        <div className="niuu:grid niuu:grid-cols-3 niuu:gap-4" data-testid="project-workspaces">
          {cards.map((card) => (
            <RealmCard
              key={card.realm.slug}
              realm={card.realm}
              resident={card.resident}
              ravn={card.ravn}
              environment={card.environment}
              binding={card.binding}
              boardName={card.boardName}
              pendingReviews={card.pendingReviews}
              waitingTickets={card.waitingTickets.length}
              runningSessions={card.runningSessions}
            />
          ))}
        </div>
      )}

      <div className="niuu:grid niuu:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] niuu:gap-4">
        <div className="niuu:flex niuu:min-w-0 niuu:flex-col niuu:gap-4">
          <SectionCard title={`Needs you · ${waiting}`}>
            <NeedsYouError error={home.needsYouError} />
            {waiting === 0 ? (
              <span className="niuu:text-xs niuu:text-text-faint">Nothing waiting on you.</span>
            ) : (
              <>
                {tickets.map(({ item, card }) => (
                  <TicketReviewRow key={item.taskId} item={item} workspace={card.realm.name} />
                ))}
                {needsYou.map((item) => (
                  <NeedsYouRow
                    key={item.itemId}
                    item={item}
                    card={
                      cards.find((card) => card.resident?.environmentId === item.environmentId) ??
                      null
                    }
                  />
                ))}
              </>
            )}
          </SectionCard>
          <SectionCard title={`Sessions · ${projectSessions.length}`}>
            <SessionsTable sessions={projectSessions} />
          </SectionCard>
        </div>
        {project ? <SharedSettings project={project} /> : null}
      </div>
    </div>
  );
}
