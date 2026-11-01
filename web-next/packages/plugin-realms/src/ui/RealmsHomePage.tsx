import { useMemo, useState } from 'react';
import { Link, useNavigate } from '@tanstack/react-router';
import { SectionCard } from '@niuulabs/plugin-volundr';
import { Chip, EmptyState, ErrorState, LoadingState, Modal, ProjectIcon } from '@niuulabs/ui';
import { useForgeProjects } from '../application/useProjects';
import { orderCards, useRealmsHome } from '../application/useRealmsHome';
import { FIRST_REALM_WALKTHROUGH, useWalkthrough } from '../application/useWalkthrough';
import { countWords } from '../domain/homeCopy';
import { groupByProject } from '../domain/projects';
import { REALM_TEMPLATES } from '../domain/templates';
import { AdoptResidents } from './AdoptResidents';
import { TemplateIcon } from './icons';
import { NeedsYouRow } from './NeedsYouRow';
import { RealmCard } from './RealmCard';
import { SentenceComposer } from './SentenceComposer';
import { NeedsYouError, TicketReviewRow } from './TicketReviewRow';

export type RealmsHomeView = 'all' | 'needs-you' | 'templates';

/** The panel is a prompt, not the inbox: the rest is one click away. */
const NEEDS_YOU_PREVIEW = 5;

const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-text-primary';
const PRIMARY =
  'niuu:rounded-md niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:font-medium niuu:text-brand-300';
const LINK = 'niuu:text-[11px] niuu:text-brand-300';

const GROUP_TITLE =
  'niuu:m-0 niuu:flex niuu:items-center niuu:gap-1.5 niuu:text-sm niuu:font-semibold niuu:text-text-primary';

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
                {template.keepsDoing.map((line) => (
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
  const projects = useForgeProjects();
  const groups = useMemo(
    () => groupByProject(orderCards(home.cards), projects.data ?? []),
    [home.cards, projects.data],
  );
  const cardFor = (environmentId: string) =>
    home.cards.find((card) => card.resident?.environmentId === environmentId) ?? null;
  const waiting = home.pendingReviews.length + home.waitingTickets.length;
  const needsYouRows = [
    ...home.waitingTickets.map(({ item, card }) => (
      <TicketReviewRow key={item.taskId} item={item} workspace={card.realm.name} />
    )),
    ...home.pendingReviews.map((item) => (
      <NeedsYouRow key={item.itemId} item={item} card={cardFor(item.environmentId)} />
    )),
  ];

  return (
    <div
      className="niuu:flex niuu:h-full niuu:flex-col niuu:gap-5 niuu:overflow-auto niuu:px-10 niuu:py-7"
      data-testid="realms-home"
    >
      <header className="niuu:flex niuu:items-end niuu:justify-between niuu:gap-6">
        <div className="niuu:flex niuu:max-w-3xl niuu:flex-col niuu:gap-1.5">
          <span className="niuu:font-mono niuu:text-[11px] niuu:uppercase niuu:tracking-[0.3em] niuu:text-brand-300">
            workspaces
          </span>
          <h1 className="niuu:m-0 niuu:text-2xl niuu:font-bold niuu:tracking-tight niuu:text-text-primary">
            {home.error
              ? 'Workspaces'
              : home.cards.length === 0
                ? 'No workspaces yet. Start with one sentence.'
                : `${countWords(home.cards.length)} workspace${home.cards.length === 1 ? '' : 's'}, each kept by a resident.`}
          </h1>
          <p className="niuu:m-0 niuu:text-[15px] niuu:text-text-secondary">
            A workspace is one resident and the job you give it, grouped by the project it serves.
            It works its tickets in sessions, watches health and keeps learning. You review what it
            asks you to.
          </p>
        </div>
        <div className="niuu:flex niuu:gap-2">
          <button type="button" className={BUTTON} onClick={() => setCloneOpen(true)}>
            Clone a workspace
          </button>
          <Link to="/realms/new" className={BUTTON}>
            Set one up step by step
          </Link>
        </div>
      </header>

      <SentenceComposer />

      {home.error ? (
        <ErrorState title="Could not load workspaces" message={String(home.error)} />
      ) : home.isLoading ? (
        <LoadingState label="Loading workspaces…" />
      ) : view === 'templates' ? (
        <TemplatesView />
      ) : view === 'needs-you' ? (
        <SectionCard title="Needs you" description="Every open question from every workspace.">
          <NeedsYouError error={home.needsYouError} />
          {waiting === 0 ? (
            <EmptyState
              title="Nothing waiting on you"
              description="Residents ask here when a grant says so, and tickets stop here at a gate."
            />
          ) : (
            needsYouRows
          )}
        </SectionCard>
      ) : (
        <>
          {home.cards.length === 0 ? (
            <EmptyState
              title="No workspaces yet"
              description="Type a sentence above, or set one up step by step."
              action={
                <Link to="/realms/new" className={PRIMARY}>
                  Set one up step by step
                </Link>
              }
            />
          ) : (
            <div className="niuu:flex niuu:flex-col niuu:gap-5" data-testid="realm-cards">
              {groups.map((group) => {
                const only = group.cards.length === 1 ? group.cards[0] : null;
                return (
                  <section
                    key={group.id ?? 'none'}
                    className="niuu:flex niuu:flex-col niuu:gap-2"
                    aria-label={group.name}
                    data-testid={`project-group-${group.id ?? 'none'}`}
                  >
                    <div className="niuu:flex niuu:items-baseline niuu:justify-between niuu:gap-3">
                      {group.id === null ? (
                        <h2 className={GROUP_TITLE}>{group.name}</h2>
                      ) : (
                        <h2 className={GROUP_TITLE}>
                          <ProjectIcon size={14} className="niuu:text-brand-300" />
                          {only ? (
                            <Link to="/realms/$slug" params={{ slug: only.realm.slug }}>
                              {group.name}
                            </Link>
                          ) : (
                            <Link
                              to={'/realms/projects/$projectId' as never}
                              params={{ projectId: group.id } as never}
                            >
                              {group.name}
                            </Link>
                          )}
                        </h2>
                      )}
                      <span className="niuu:text-xs niuu:text-text-muted">
                        {group.cards.length} workspace{group.cards.length === 1 ? '' : 's'}
                      </span>
                    </div>
                    <div className="niuu:grid niuu:grid-cols-3 niuu:gap-4">
                      {group.cards.map((card) => (
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
                  </section>
                );
              })}
            </div>
          )}
          <AdoptResidents residents={home.unclaimedResidents} />
          <div className="niuu:grid niuu:min-h-0 niuu:flex-1 niuu:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] niuu:gap-4">
            <div className="niuu:flex niuu:min-h-0 niuu:flex-col niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-4 niuu:pb-1.5 niuu:pt-3.5">
              <div className="niuu:flex niuu:items-center niuu:justify-between niuu:pb-1">
                <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                  Needs you
                </span>
                <span className="niuu:text-xs niuu:text-text-muted">
                  {waiting} open ·{' '}
                  <Link to="/realms/needs-you" className={LINK}>
                    inbox
                  </Link>
                </span>
              </div>
              <NeedsYouError error={home.needsYouError} />
              <div className="niuu:min-h-0 niuu:overflow-auto">
                {waiting === 0 ? (
                  <span className="niuu:block niuu:py-3 niuu:text-xs niuu:text-text-faint">
                    Nothing waiting on you.
                  </span>
                ) : (
                  needsYouRows.slice(0, NEEDS_YOU_PREVIEW)
                )}
              </div>
            </div>
            <div className="niuu:flex niuu:min-h-0 niuu:flex-col niuu:gap-4">
              <div className="niuu:flex niuu:flex-col niuu:gap-2.5 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-4">
                <div className="niuu:flex niuu:items-center niuu:justify-between">
                  <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                    Across workspaces
                  </span>
                  <span className="niuu:font-mono niuu:text-[11px] niuu:text-text-faint">now</span>
                </div>
                <div className="niuu:grid niuu:grid-cols-2 niuu:gap-x-4 niuu:gap-y-3">
                  <Figure value={home.cards.length} label="workspaces" />
                  <Figure value={home.residentsOnline} label="residents online" />
                  <Figure value={home.sessionsRunning} label="sessions running" />
                  <Figure value={waiting} label="awaiting you" attention={waiting > 0} />
                </div>
              </div>
              <div className="niuu:flex niuu:min-h-0 niuu:flex-1 niuu:flex-col niuu:gap-2 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-4">
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

      <Modal
        open={cloneOpen}
        onOpenChange={setCloneOpen}
        title="Clone a workspace"
        description="Only the blanks change: repository, board, name. Charter, trust, standing jobs and template come from the workspace you pick."
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
