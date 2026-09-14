import { useState, type ReactNode } from 'react';
import { Link, useNavigate } from '@tanstack/react-router';
import { Boxes, MessageCircleQuestion, Terminal, Workflow } from 'lucide-react';
import { useSetUiMode } from '@niuulabs/shell';
import { reviewKindLabel, useDecideReview, type ReviewItem } from '@niuulabs/plugin-valkyrie';
import type { Session } from '@niuulabs/plugin-volundr';
import { Chip, EmptyState, ErrorState, LoadingState, StateDot } from '@niuulabs/ui';
import { useDomainSessions, useHomeStatus } from '../application/useHome';
import { orderCards, useRealmsHome, type RealmHomeCard } from '../application/useRealmsHome';
import { greeting, stateSentence } from '../domain/homeCopy';
import { continueSessions, isLive, needsYouSessions, sessionLabel } from '../domain/homeSessions';
import { TemplateIcon, agoLabel } from '../ui/icons';

const CARD =
  'niuu:flex niuu:flex-col niuu:gap-3.5 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-5';
const PANEL =
  'niuu:flex niuu:min-h-0 niuu:flex-col niuu:gap-1 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-4 niuu:py-3.5';
const PRIMARY =
  'niuu:rounded-md niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-brand-300 niuu:disabled:opacity-40';
const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:font-medium niuu:text-text-primary niuu:disabled:opacity-40';
const LINK = 'niuu:text-[11px] niuu:text-brand-300';
const ROW =
  'niuu:flex niuu:items-center niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:py-2.5 niuu:last:border-b-0';

const CONTINUE_LIMIT = 3;
/** The panel is a prompt, not the inbox: the rest is one click away. */
const NEEDS_YOU_LIMIT = 5;
const WAKEFUL = new Set(['wakeful', 'watching']);

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
    title: 'Set up a realm',
    blurb:
      'A resident that keeps an environment: the board, the sessions, the quality, the health.',
    bullets: ['Starts from a template', 'Works while you are away', 'Asks you only what it must'],
    action: 'Set up a realm',
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

function ReviewRow({
  item,
  card,
  busy,
  onDecide,
}: {
  item: ReviewItem;
  card: RealmHomeCard | null;
  busy: boolean;
  onDecide: (item: ReviewItem, decision: 'approved' | 'rejected') => void;
}) {
  return (
    <div className={ROW} data-testid={`home-needs-you-${item.itemId}`}>
      <span className="niuu:flex niuu:h-7 niuu:w-7 niuu:shrink-0 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-status-amber/40 niuu:bg-status-amber/10 niuu:text-status-amber">
        <MessageCircleQuestion size={13} aria-hidden="true" />
      </span>
      <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
        <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">{item.title}</span>
        <span className="niuu:truncate niuu:text-xs niuu:text-text-muted">
          {card ? `${card.realm.name} · ` : ''}
          {item.summary}
        </span>
      </div>
      <Chip tone="muted">{reviewKindLabel(item.kind)}</Chip>
      <button
        type="button"
        className={PRIMARY}
        disabled={busy}
        onClick={() => onDecide(item, 'approved')}
      >
        Approve
      </button>
      <button
        type="button"
        className={BUTTON}
        disabled={busy}
        onClick={() => onDecide(item, 'rejected')}
      >
        Reject
      </button>
      {card ? (
        <Link to="/realms/$slug" params={{ slug: card.realm.slug }} className={LINK}>
          Open
        </Link>
      ) : (
        <Link to={'/valkyrie/inbox' as never} className={LINK}>
          Open
        </Link>
      )}
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
  const acted = agoLabel(card.resident?.lastActionAt);
  return (
    <div className={ROW} data-testid={`home-continue-realm-${card.realm.slug}`}>
      <span className="niuu:flex niuu:h-7 niuu:w-7 niuu:shrink-0 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-brand/40 niuu:bg-brand/10 niuu:text-brand">
        <TemplateIcon templateId={card.binding?.template} size={13} />
      </span>
      <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
        <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">{card.realm.name}</span>
        <span className="niuu:truncate niuu:text-xs niuu:text-text-muted">
          {card.resident?.wakefulness ?? 'no resident'}
          {acted ? ` · acted ${acted}` : ''}
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
 * The Simple-mode landing page: what do you want to do, what is waiting on you,
 * and what you were in the middle of. Everything here links somewhere that already
 * exists; the page owns no state of its own beyond what it has just decided.
 */
export function HomePage() {
  const home = useRealmsHome();
  const status = useHomeStatus();
  const sessions = useDomainSessions();
  const decide = useDecideReview();
  const setUiMode = useSetUiMode();
  const navigate = useNavigate();
  const [decided, setDecided] = useState<string[]>([]);
  const [switchError, setSwitchError] = useState<string | null>(null);

  const pending = home.pendingReviews.filter((item) => !decided.includes(item.itemId));
  const blocked = needsYouSessions(sessions.data);
  const recent = continueSessions(sessions.data, CONTINUE_LIMIT);
  const live = (sessions.data ?? []).filter(isLive).length;
  const wakefulRealms = orderCards(home.cards)
    .filter((card) => WAKEFUL.has(card.resident?.wakefulness ?? ''))
    .slice(0, CONTINUE_LIMIT);
  const cardFor = (environmentId: string) =>
    home.cards.find((card) => card.resident?.environmentId === environmentId) ?? null;

  function answer(item: ReviewItem, decision: 'approved' | 'rejected') {
    setDecided((ids) => [...ids, item.itemId]);
    decide.mutate(
      { itemId: item.itemId, decision },
      { onError: () => setDecided((ids) => ids.filter((id) => id !== item.itemId)) },
    );
  }

  function openDashboard() {
    setSwitchError(null);
    setUiMode('advanced')
      .then(() => navigate({ to: '/volundr/forge' as never }))
      .catch((cause: unknown) =>
        setSwitchError(cause instanceof Error ? cause.message : String(cause)),
      );
  }

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
                  realms: home.cards.length,
                  running: live,
                  needsYou: pending.length + blocked.length,
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

      <div className="niuu:grid niuu:grid-cols-3 niuu:gap-5">
        {CHOICES.map((choice) => (
          <ChoiceCard key={choice.id} choice={choice} />
        ))}
      </div>

      {home.error ? (
        <ErrorState title="Could not load your realms" message={String(home.error)} />
      ) : home.isLoading ? (
        <LoadingState label="Looking at what is running…" />
      ) : (
        <div className="niuu:grid niuu:min-h-0 niuu:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] niuu:gap-5">
          <Panel
            title="Needs you"
            testId="home-needs-you"
            aside={
              <span className="niuu:text-xs niuu:text-text-muted">
                {pending.length + blocked.length} open ·{' '}
                <Link to="/realms/needs-you" className={LINK}>
                  inbox
                </Link>
              </span>
            }
          >
            {pending.length + blocked.length === 0 ? (
              <span className="niuu:py-3 niuu:text-xs niuu:text-text-faint">
                Nothing is waiting on you.
              </span>
            ) : (
              <>
                {pending.slice(0, NEEDS_YOU_LIMIT).map((item) => (
                  <ReviewRow
                    key={item.itemId}
                    item={item}
                    card={cardFor(item.environmentId)}
                    busy={decide.isPending}
                    onDecide={answer}
                  />
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
            {decide.error ? (
              <span className="niuu:pt-2 niuu:text-xs niuu:text-critical-fg">
                {String(decide.error)}
              </span>
            ) : null}
          </Panel>

          <Panel title="Continue" testId="home-continue">
            {recent.length === 0 && wakefulRealms.length === 0 ? (
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
                {wakefulRealms.map((card) => (
                  <RealmRow key={card.realm.slug} card={card} />
                ))}
              </>
            )}
          </Panel>
        </div>
      )}

      <div className="niuu:flex niuu:items-center niuu:gap-3">
        <button
          type="button"
          className={`${LINK} niuu:self-start`}
          onClick={openDashboard}
          data-testid="home-open-advanced"
        >
          Skip this and open the dashboard (Advanced) ›
        </button>
        {switchError ? (
          <span className="niuu:text-[11px] niuu:text-critical-fg" role="alert">
            {switchError}
          </span>
        ) : null}
      </div>
    </div>
  );
}
