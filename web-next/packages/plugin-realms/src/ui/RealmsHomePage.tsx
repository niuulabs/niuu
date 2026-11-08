import { useMemo, useState } from 'react';
import { Link, useNavigate } from '@tanstack/react-router';
import { SectionCard } from '@niuulabs/plugin-volundr';
import { Chip, EmptyState, ErrorState, LoadingState, Modal, ProjectIcon } from '@niuulabs/ui';
import { useCollapsedProjects } from '../application/useCollapsedProjects';
import { useForgeProjects } from '../application/useProjects';
import { orderCards, useRealmsHome } from '../application/useRealmsHome';
import { FIRST_REALM_WALKTHROUGH, useWalkthrough } from '../application/useWalkthrough';
import { countWords } from '../domain/homeCopy';
import { groupByProject, projectsWithoutWorkspaces } from '../domain/projects';
import { REALM_TEMPLATES } from '../domain/templates';
import { TemplateIcon } from './icons';
import { NewProjectDialog } from './NewProjectDialog';
import { ProjectGroupSection } from './ProjectGroupSection';
import { SentenceComposer } from './SentenceComposer';
import { NeedsYouError, TicketReviewRow } from './TicketReviewRow';

export type RealmsHomeView = 'all' | 'needs-you' | 'templates';

/** The panel is a prompt, not the full list: the rest is one click away. */
const NEEDS_YOU_PREVIEW = 5;

const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-text-primary';
const PRIMARY =
  'niuu:rounded-md niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:font-medium niuu:text-brand-300';
const CREATE =
  'niuu:rounded-md niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-brand-300';
const LINK = 'niuu:text-[11px] niuu:text-brand-300';

function TemplatesView() {
  return (
    <div className="niuu:grid niuu:grid-cols-2 niuu:gap-4">
      {REALM_TEMPLATES.map((template) => {
        return (
          <SectionCard
            key={template.id}
            title={template.name}
            description={template.blurb}
            icon={<TemplateIcon templateId={template.id} size={16} />}
          >
            <div className="niuu:flex niuu:flex-col niuu:gap-3">
              <ul className="niuu:m-0 niuu:flex niuu:list-none niuu:flex-col niuu:gap-1 niuu:p-0 niuu:text-xs niuu:text-text-secondary">
                {template.covers.map((line) => (
                  <li key={line}>· {line}</li>
                ))}
              </ul>
              <div className="niuu:flex niuu:flex-wrap niuu:gap-1.5">
                {template.needs.map((need) => (
                  <Chip key={need} tone="muted">
                    {need}
                  </Chip>
                ))}
              </div>
              <Link
                to="/realms/new"
                search={{ template: template.id } as never}
                className={`${PRIMARY} niuu:self-start`}
              >
                Use this template
              </Link>
            </div>
          </SectionCard>
        );
      })}
    </div>
  );
}

function Figure({
  value,
  label,
  attention,
}: {
  value: number;
  label: string;
  attention?: boolean;
}) {
  return (
    <div className="niuu:flex niuu:min-w-0 niuu:flex-col">
      <span
        className={`niuu:font-mono niuu:text-lg niuu:font-semibold niuu:leading-tight ${attention ? 'niuu:text-status-amber' : 'niuu:text-text-primary'}`}
      >
        {value}
      </span>
      <span className="niuu:text-[11px] niuu:text-text-muted">{label}</span>
    </div>
  );
}

export function RealmsHomePage({ view = 'all' }: { view?: RealmsHomeView }) {
  const navigate = useNavigate();
  const home = useRealmsHome();
  const walkthrough = useWalkthrough(FIRST_REALM_WALKTHROUGH);
  const [cloneOpen, setCloneOpen] = useState(false);
  const [newProjectOpen, setNewProjectOpen] = useState(false);
  const collapsed = useCollapsedProjects();
  const projects = useForgeProjects();
  const groups = useMemo(
    () => groupByProject(orderCards(home.cards), projects.data ?? []),
    [home.cards, projects.data],
  );
  const emptyProjects = useMemo(
    () => projectsWithoutWorkspaces(home.cards, projects.data ?? []),
    [home.cards, projects.data],
  );
  const waiting = home.waitingTickets.length;
  const needsYouRows = home.waitingTickets.map(({ item, card }) => (
    <TicketReviewRow key={item.taskId} item={item} workspace={card.realm.name} />
  ));

  return (
    <div
      className="niuu:flex niuu:h-full niuu:flex-col niuu:gap-5 niuu:overflow-auto niuu:px-10 niuu:py-7"
      data-testid="realms-home"
    >
      <header className="niuu:flex niuu:items-end niuu:justify-between niuu:gap-6">
        <div className="niuu:flex niuu:max-w-3xl niuu:flex-col niuu:gap-1.5">
          <span className="niuu:font-mono niuu:text-[11px] niuu:uppercase niuu:tracking-[0.3em] niuu:text-brand-300">
            projects
          </span>
          <h1 className="niuu:m-0 niuu:text-2xl niuu:font-bold niuu:tracking-tight niuu:text-text-primary">
            {home.error
              ? 'Projects'
              : home.cards.length === 0
                ? 'No projects yet. Start with one sentence.'
                : `${countWords(home.cards.length)} workspace${home.cards.length === 1 ? '' : 's'} across your projects.`}
          </h1>
          <p className="niuu:m-0 niuu:text-[15px] niuu:text-text-secondary">
            A project is what you work on, and its workspaces sit under it. A workspace is a home
            for one job: its board, its sessions and its memory. You review what stops at a gate.
          </p>
        </div>
        <div className="niuu:flex niuu:shrink-0 niuu:gap-2">
          <button type="button" className={BUTTON} onClick={() => setCloneOpen(true)}>
            Clone a workspace
          </button>
          <button type="button" className={BUTTON} onClick={() => setNewProjectOpen(true)}>
            New project
          </button>
          <Link to="/realms/new" className={CREATE}>
            New workspace
          </Link>
        </div>
      </header>

      <SentenceComposer />

      {home.error ? (
        <ErrorState title="Could not load projects" message={String(home.error)} />
      ) : home.isLoading ? (
        <LoadingState label="Loading projects…" />
      ) : view === 'templates' ? (
        <TemplatesView />
      ) : view === 'needs-you' ? (
        <SectionCard
          title="Needs you"
          description="Every ticket waiting at a gate in every workspace."
        >
          <NeedsYouError error={home.needsYouError} />
          {waiting === 0 ? (
            <EmptyState title="Nothing waiting on you" description="Tickets stop here at a gate." />
          ) : (
            needsYouRows
          )}
        </SectionCard>
      ) : (
        <>
          {home.cards.length === 0 ? (
            <EmptyState
              title="No projects yet"
              description="Type a sentence above to start a workspace, or set one up step by step."
              action={
                <Link to="/realms/new" className={PRIMARY}>
                  Set one up step by step
                </Link>
              }
            />
          ) : (
            <div className="niuu:flex niuu:flex-col niuu:gap-5" data-testid="realm-cards">
              {groups.map((group) => (
                <ProjectGroupSection
                  key={group.id ?? 'none'}
                  group={group}
                  collapsed={collapsed.isCollapsed(group.id)}
                  onToggle={() => collapsed.toggle(group.id)}
                />
              ))}
            </div>
          )}
          {emptyProjects.length > 0 ? (
            <div
              className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-x-3 niuu:gap-y-1 niuu:text-xs niuu:text-text-muted"
              data-testid="empty-projects"
            >
              <span>No workspaces yet:</span>
              {emptyProjects.map((project) => (
                <Link
                  key={project.id}
                  to={'/realms/projects/$projectId' as never}
                  params={{ projectId: project.id } as never}
                  className="niuu:inline-flex niuu:items-center niuu:gap-1 niuu:text-brand-300"
                >
                  <ProjectIcon size={12} />
                  {project.name}
                </Link>
              ))}
            </div>
          ) : null}
          <div className="niuu:grid niuu:flex-1 niuu:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] niuu:gap-4">
            <div
              className="niuu:flex niuu:flex-col niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-4 niuu:pb-1.5 niuu:pt-3.5"
              data-testid="needs-you-panel"
            >
              <div className="niuu:flex niuu:items-center niuu:justify-between niuu:pb-1">
                <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                  Needs you
                </span>
                <span className="niuu:text-xs niuu:text-text-muted">
                  {waiting} open ·{' '}
                  <Link to="/realms/needs-you" className={LINK}>
                    see all
                  </Link>
                </span>
              </div>
              <NeedsYouError error={home.needsYouError} />
              {waiting === 0 ? (
                <span className="niuu:block niuu:py-3 niuu:text-xs niuu:text-text-faint">
                  Nothing waiting on you.
                </span>
              ) : (
                needsYouRows.slice(0, NEEDS_YOU_PREVIEW)
              )}
            </div>
            <div className="niuu:flex niuu:flex-col niuu:gap-4">
              <div className="niuu:flex niuu:flex-col niuu:gap-2.5 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-4">
                <div className="niuu:flex niuu:items-center niuu:justify-between">
                  <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                    Across workspaces
                  </span>
                  <span className="niuu:font-mono niuu:text-[11px] niuu:text-text-faint">now</span>
                </div>
                <div className="niuu:grid niuu:grid-cols-2 niuu:gap-x-4 niuu:gap-y-3">
                  <Figure value={home.cards.length} label="workspaces" />
                  <Figure value={home.sessionsRunning} label="sessions running" />
                  <Figure value={waiting} label="awaiting you" attention={waiting > 0} />
                </div>
              </div>
              <div
                className="niuu:flex niuu:flex-1 niuu:flex-col niuu:gap-2 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-4"
                data-testid="walkthroughs-panel"
              >
                <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                  Walkthroughs
                </span>
                <div className="niuu:flex niuu:items-start niuu:gap-2.5 niuu:py-1">
                  <span
                    className={`niuu:flex niuu:h-[18px] niuu:w-[18px] niuu:shrink-0 niuu:items-center niuu:justify-center niuu:rounded-full niuu:font-mono niuu:text-[10px] ${
                      walkthrough.complete
                        ? 'niuu:bg-brand niuu:text-bg-primary'
                        : 'niuu:border-2 niuu:border-brand niuu:text-brand'
                    }`}
                  >
                    {walkthrough.complete ? '✓' : '●'}
                  </span>
                  <div className="niuu:flex niuu:flex-col niuu:gap-0.5">
                    <span
                      className={`niuu:text-[13px] niuu:font-medium ${
                        walkthrough.complete
                          ? 'niuu:text-text-secondary niuu:line-through'
                          : 'niuu:text-text-primary'
                      }`}
                    >
                      {FIRST_REALM_WALKTHROUGH.title}
                    </span>
                    <span className="niuu:text-xs niuu:text-text-muted">
                      {walkthrough.done.length} of {FIRST_REALM_WALKTHROUGH.steps.length} steps
                      {walkthrough.hidden ? ' · hidden' : ''}
                    </span>
                  </div>
                </div>
                {walkthrough.hidden ? (
                  <button
                    type="button"
                    className={`${LINK} niuu:self-start`}
                    onClick={() => walkthrough.setHidden(false)}
                  >
                    Show it again
                  </button>
                ) : null}
              </div>
            </div>
          </div>
        </>
      )}

      {newProjectOpen ? <NewProjectDialog onClose={() => setNewProjectOpen(false)} /> : null}

      <Modal
        open={cloneOpen}
        onOpenChange={setCloneOpen}
        title="Clone a workspace"
        description="Only the blanks change: repository, board, name and charter. Trust and template come from the workspace you pick."
      >
        <div className="niuu:flex niuu:flex-col niuu:gap-2" data-testid="clone-picker">
          {home.cards.length === 0 ? (
            <EmptyState title="No workspace to clone yet" />
          ) : (
            home.cards.map((card) => (
              <button
                key={card.realm.slug}
                type="button"
                className={`${BUTTON} niuu:text-left`}
                onClick={() => {
                  setCloneOpen(false);
                  void navigate({ to: '/realms/new', search: { from: card.realm.slug } as never });
                }}
              >
                {card.realm.name}
                <span className="niuu:ml-2 niuu:font-mono niuu:text-[11px] niuu:text-text-muted">
                  {card.realm.slug}
                </span>
              </button>
            ))
          )}
        </div>
      </Modal>
    </div>
  );
}
