import type { ReactNode } from 'react';
import { Link } from '@tanstack/react-router';
import { Boxes, Terminal, Workflow } from 'lucide-react';
import { usePluginEnabled } from '@niuulabs/shell';
import type { Session } from '@niuulabs/plugin-volundr';
import { Chip, EmptyState, ErrorState, LoadingState, StateDot } from '@niuulabs/ui';
import { useDomainSessions, useHomeStatus } from '../application/useHome';
import { orderCards, useRealmsHome, type RealmHomeCard } from '../application/useRealmsHome';
import { greeting, stateSentence } from '../domain/homeCopy';
import { continueSessions, isLive, needsYouSessions, sessionLabel } from '../domain/homeSessions';
import { TemplateIcon } from '../ui/icons';
import { NeedsYouError, TicketReviewRow } from '../ui/TicketReviewRow';

const CARD =
  'niuu:flex niuu:flex-col niuu:gap-3.5 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-5';
const PANEL =
  'niuu:flex niuu:flex-col niuu:gap-1 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-4 niuu:py-3.5';
const PRIMARY =
  'niuu:rounded-md niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-brand-300 niuu:disabled:opacity-40';
const LINK = 'niuu:text-[11px] niuu:text-brand-300';
const ROW =
  'niuu:flex niuu:items-center niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:py-2.5 niuu:last:border-b-0';

const CONTINUE_LIMIT = 3;
/** The panel is a prompt, not the full list: the rest is one click away. */
const NEEDS_YOU_LIMIT = 5;

interface Choice {
  id: string;
  step: string;
  icon: ReactNode;
  title: string;
  blurb: string;
  bullets: string[];
  action: string;
  to: string;
}

/** The three ways to start, in the order most people want them. */
const CHOICES: Choice[] = [
  {
    id: 'session',
    step: '1',
    icon: <Terminal size={18} aria-hidden="true" />,
    title: 'Start a session',
    blurb: 'A coding agent in its own sandbox, working on one task you describe.',
    bullets: [
      'You say what needs doing',
      'It works on a branch of its own',
      'It asks before anything risky',
    ],
    action: 'Start a session',
    to: '/volundr/sessions/new',
  },
  {
    id: 'workflow',
    step: '2',
    icon: <Workflow size={18} aria-hidden="true" />,
    title: 'Run a workflow',
    blurb: 'Stages that hand work along, with gates where you approve before it goes on.',
    bullets: [
      'Pick a workflow and a repository',
      'It stops at every gate',
      'You approve or send back',
    ],
    action: 'Run a workflow',
    to: '/ting/workflows',
  },
  {
    id: 'realm',
    step: '3',
    icon: <Boxes size={18} aria-hidden="true" />,
    title: 'Set up a workspace',
    blurb: 'A home for one project: its board, its sessions and its memory.',
    bullets: ['Starts from a template', 'Works its tickets in sessions', 'Stops at gates for you'],
    action: 'Set up a workspace',
    to: '/realms/new',
  },
];

function ChoiceCard({ choice }: { choice: Choice }) {
  return (
    <section className={CARD} data-testid={`home-choice-${choice.id}`}>
      <div className="niuu:flex niuu:items-center niuu:gap-3">
        <span className="niuu:flex niuu:h-9 niuu:w-9 niuu:shrink-0 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-brand/40 niuu:bg-brand/10 niuu:text-brand">
          {choice.icon}
        </span>
        <span className="niuu:flex niuu:min-w-0 niuu:flex-col">
          <span className="niuu:font-mono niuu:text-[11px] niuu:text-text-faint">
            {choice.step}
          </span>
          <span className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
            {choice.title}
          </span>
        </span>
      </div>
      <p className="niuu:m-0 niuu:text-[13px] niuu:text-text-secondary">{choice.blurb}</p>
      <ul className="niuu:m-0 niuu:flex niuu:list-none niuu:flex-col niuu:gap-1 niuu:p-0 niuu:text-xs niuu:text-text-muted">
        {choice.bullets.map((bullet) => (
          <li key={bullet}>· {bullet}</li>
        ))}
      </ul>
      <Link to={choice.to as never} className={`${PRIMARY} niuu:self-start`}>
        {choice.action}
      </Link>
    </section>
  );
}

function StatusChips({
  models,
  providers,
  repos,
  boards,
  host,
}: {
  models: number;
  providers: number;
  repos: number;
  boards: number | null;
  host: string;
}) {
  return (
    <div
      className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-1.5"
      data-testid="home-chips"
    >
      <Chip tone={models > 0 ? 'brand' : 'muted'}>
        <StateDot state={models > 0 ? 'healthy' : 'unknown'} size={6} />
        {models} models · {providers} providers
      </Chip>
      <Chip tone="muted">{repos} repositories</Chip>
      {boards === null ? null : <Chip tone="muted">{boards} boards</Chip>}
      <Chip tone="muted">{host}</Chip>
    </div>
  );
}

function SessionRow({ session, testId }: { session: Session; testId: string }) {
  return (
    <div className={ROW} data-testid={testId}>
      <span className="niuu:flex niuu:h-7 niuu:w-7 niuu:shrink-0 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-brand/40 niuu:bg-brand/10 niuu:text-brand">
        <Terminal size={13} aria-hidden="true" />
      </span>
      <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
        <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">
          {sessionLabel(session)}
        </span>
        <span className="niuu:truncate niuu:text-xs niuu:text-text-muted">
          {session.state}
          {session.preview ? ` · ${session.preview}` : ''}
        </span>
      </div>
      <Link
        to={'/volundr/sessions/$sessionId' as never}
        params={{ sessionId: session.id } as never}
        className={LINK}
      >
        Open
      </Link>
    </div>
  );
}

function RealmRow({ card }: { card: RealmHomeCard }) {
  return (
    <div className={ROW} data-testid={`home-continue-realm-${card.realm.slug}`}>
      <span className="niuu:flex niuu:h-7 niuu:w-7 niuu:shrink-0 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-brand/40 niuu:bg-brand/10 niuu:text-brand">
        <TemplateIcon templateId={card.binding?.template} size={13} />
      </span>
      <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
        <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">{card.realm.name}</span>
        <span className="niuu:truncate niuu:text-xs niuu:text-text-muted">
          {card.runningSessions} running of {card.totalSessions} sessions
        </span>
      </div>
      <Link to="/realms/$slug" params={{ slug: card.realm.slug }} className={LINK}>
        Open
      </Link>
    </div>
  );
}

function Panel({
  title,
  aside,
  children,
  testId,
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
  testId: string;
}) {
  return (
    <section className={PANEL} data-testid={testId}>
      <div className="niuu:flex niuu:items-center niuu:justify-between niuu:pb-1">
        <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">{title}</span>
        {aside}
      </div>
      {children}
    </section>
  );
}

/**
 * The landing page: what do you want to do, what is waiting on you,
 * and what you were in the middle of. Everything here links somewhere that already
 * exists; the page owns no state of its own.
 */
export function HomePage() {
  const home = useRealmsHome();
  const realmsEnabled = usePluginEnabled('realms');
  const status = useHomeStatus();
  const sessions = useDomainSessions();

  const blocked = needsYouSessions(sessions.data);
  const recent = continueSessions(sessions.data, CONTINUE_LIMIT);
  const live = (sessions.data ?? []).filter(isLive).length;
  // With Realms off its pages have no routes, so nothing here may point at them.
  const realmCards = realmsEnabled ? home.cards : [];
  const tickets = realmsEnabled ? home.waitingTickets : [];
  const waiting = blocked.length + tickets.length;
  const choices = realmsEnabled ? CHOICES : CHOICES.filter((choice) => choice.id !== 'realm');
  const activeRealms = orderCards(realmCards)
    .filter((card) => card.runningSessions > 0)
    .slice(0, CONTINUE_LIMIT);

  return (
    <div
      className="niuu:flex niuu:h-full niuu:flex-col niuu:gap-7 niuu:overflow-auto niuu:px-12 niuu:py-9"
      data-testid="home-page"
    >
      <header className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-6">
        <div className="niuu:flex niuu:max-w-2xl niuu:flex-col niuu:gap-1.5">
          <h1 className="niuu:m-0 niuu:text-3xl niuu:font-bold niuu:tracking-tight niuu:text-text-primary">
            {greeting(status.identity?.displayName)}
          </h1>
          <p className="niuu:m-0 niuu:text-[15px] niuu:text-text-secondary">
            {home.error
              ? 'Could not read what is running.'
              : stateSentence({
                  realms: realmCards.length,
                  running: live,
                  needsYou: waiting,
                })}
          </p>
        </div>
        <StatusChips
          models={status.models}
          providers={status.providers}
          repos={status.repos}
          boards={status.boards}
          host={status.host}
        />
      </header>

      <div
        className={`niuu:grid niuu:gap-5 ${realmsEnabled ? 'niuu:grid-cols-3' : 'niuu:grid-cols-2'}`}
      >
        {choices.map((choice) => (
          <ChoiceCard key={choice.id} choice={choice} />
        ))}
      </div>

      {home.error ? (
        <ErrorState title="Could not load your workspaces" message={String(home.error)} />
      ) : home.isLoading ? (
        <LoadingState label="Looking at what is running…" />
      ) : (
        <div className="niuu:grid niuu:grid-cols-1 niuu:gap-5 niuu:xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
          <Panel
            title="Needs you"
            testId="home-needs-you"
            aside={
              <span className="niuu:text-xs niuu:text-text-muted">
                {waiting} open
                {realmsEnabled ? (
                  <>
                    {' '}
                    ·{' '}
                    <Link to="/realms/needs-you" className={LINK}>
                      see all
                    </Link>
                  </>
                ) : null}
              </span>
            }
          >
            <NeedsYouError error={home.needsYouError} />
            {waiting === 0 ? (
              <span className="niuu:py-3 niuu:text-xs niuu:text-text-faint">
                Nothing is waiting on you.
              </span>
            ) : (
              <>
                {tickets.slice(0, NEEDS_YOU_LIMIT).map(({ item, card }) => (
                  <TicketReviewRow key={item.taskId} item={item} workspace={card.realm.name} />
                ))}
                {blocked.slice(0, NEEDS_YOU_LIMIT).map((session) => (
                  <SessionRow
                    key={session.id}
                    session={session}
                    testId={`home-needs-you-session-${session.id}`}
                  />
                ))}
              </>
            )}
          </Panel>

          <Panel title="Continue" testId="home-continue">
            {recent.length === 0 && activeRealms.length === 0 ? (
              <EmptyState
                title="Nothing to go back to yet"
                description="Whatever you start shows up here."
              />
            ) : (
              <>
                {recent.map((session) => (
                  <SessionRow
                    key={session.id}
                    session={session}
                    testId={`home-continue-session-${session.id}`}
                  />
                ))}
                {activeRealms.map((card) => (
                  <RealmRow key={card.realm.slug} card={card} />
                ))}
              </>
            )}
          </Panel>
        </div>
      )}
    </div>
  );
}
