/**
 * ResidentsPage — the Simple-mode board: who keeps what.
 *
 * One card per resident, then the personas they run with. Everything here is
 * served by ports that already exist; the Valkyrie side (how awake a resident
 * is, what it last did, what is waiting) is read through optional services and
 * simply left out when those are not registered.
 */

import { useMemo, useState } from 'react';
import { Link, useNavigate } from '@tanstack/react-router';
import { Bot, MessageSquare, Pause, Play, Plus, RotateCw } from 'lucide-react';
import {
  Chip,
  EmptyState,
  ErrorState,
  LoadingState,
  PersonaAvatar,
  StateDot,
  relTime,
  type DotState,
} from '@niuulabs/ui';
import type { Ravn } from '../domain/ravn';
import type { Session } from '../domain/session';
import type { PersonaSummary } from '../ports';
import {
  canCreateResidentSession,
  canRestartResident,
  canResumeResident,
  canSuspendResident,
  isResidentRavn,
  nameForRavn,
  ravnKey,
} from '../domain/residentActions';
import {
  latestActionFor,
  orderResidents,
  pendingReviewsFor,
  personaUsage,
  realmForRavn,
  residentActivity,
  valkyrieForRavn,
  type RealmView,
  type ResidentActivity,
  type ValkyrieResidentView,
} from '../domain/residentBoard';
import { useRavens } from './hooks/useRavens';
import { useSessions } from './hooks/useSessions';
import { useCreateResidentSession, useResidentLifecycle } from './hooks/useResidentControl';
import {
  useOptionalPendingReviews,
  useOptionalRealms,
  useOptionalValkyrieDashboard,
} from './hooks/useResidentContext';
import { usePersonas } from './usePersonas';
import { ravnStatusToDotState } from './grouping';
import { dispatchSessionSelection } from './sessionSelection';
import { ResidentDeployDialog } from './ResidentDeployDialog';
import { saveStorage } from './storage';

const LEDE_FIRST =
  'A resident keeps one realm: it reads the board, starts sessions, checks quality and asks you when it should.';
const LEDE_SECOND = 'A persona is the character it runs with.';

const PERSONA_STORAGE_KEY = 'ravn.persona';
const PERSONA_SELECTED_EVENT = 'ravn:persona-selected';

const CARD =
  'niuu:flex niuu:flex-col niuu:gap-3 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-4 niuu:py-3.5';
const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:font-medium niuu:text-text-primary niuu:disabled:opacity-40';
const PRIMARY =
  'niuu:flex niuu:items-center niuu:gap-1.5 niuu:rounded-md niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:font-medium niuu:text-brand-300 niuu:disabled:opacity-40';
const FOOTER_ACTION =
  'niuu:flex niuu:items-center niuu:gap-1.5 niuu:text-[11px] niuu:text-text-muted niuu:hover:text-text-primary niuu:disabled:opacity-40';
const LINK = 'niuu:text-[11px] niuu:text-brand-300';

/** How awake a resident is, as the Valkyrie dashboard reports it. */
const WAKEFULNESS_DOT: Record<string, DotState> = {
  wakeful: 'healthy',
  watching: 'observing',
  dreaming: 'processing',
  sleeping: 'unknown',
};

interface ResidentState {
  dot: DotState;
  label: string;
  pulse: boolean;
}

function residentState(ravn: Ravn, valkyrie: ValkyrieResidentView | null): ResidentState {
  if (valkyrie) {
    return {
      dot: WAKEFULNESS_DOT[valkyrie.wakefulness] ?? 'unknown',
      label: valkyrie.wakefulness,
      pulse: valkyrie.wakefulness === 'wakeful',
    };
  }
  return {
    dot: ravnStatusToDotState(ravn.status),
    label: ravn.status,
    pulse: ravn.status === 'active',
  };
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

interface ResidentCardProps {
  ravn: Ravn;
  realm: RealmView | null;
  valkyrie: ValkyrieResidentView | null;
  pendingCount: number;
  activity: ResidentActivity | null;
  session: Session | null;
}

function ResidentCard({
  ravn,
  realm,
  valkyrie,
  pendingCount,
  activity,
  session,
}: ResidentCardProps) {
  const lifecycle = useResidentLifecycle();
  const createSession = useCreateResidentSession(ravn);
  const name = nameForRavn(ravn);
  const state = residentState(ravn, valkyrie);
  const canTalk = Boolean(session) || canCreateResidentSession(ravn);

  async function talk() {
    if (session) {
      dispatchSessionSelection(session);
      return;
    }
    let created: Session;
    try {
      created = await createSession.mutateAsync({ title: `Talk with ${name}` });
    } catch {
      return;
    }
    dispatchSessionSelection(created);
  }

  return (
    <article className={CARD} data-testid={`resident-card-${ravnKey(ravn)}`}>
      <div className="niuu:flex niuu:items-start niuu:gap-2.5">
        <PersonaAvatar
          role={ravn.role ?? 'build'}
          letter={ravn.letter ?? name.charAt(0).toUpperCase()}
          size={28}
        />
        <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
          <span className="niuu:truncate niuu:text-[15px] niuu:font-medium niuu:text-text-primary">
            {name}
          </span>
          {realm ? (
            <Link
              to={`/realms/${realm.slug}`}
              className="niuu:truncate niuu:text-[11px] niuu:text-brand-300"
              title={`Open ${realm.name}`}
            >
              keeps {realm.name}
            </Link>
          ) : (
            <span className="niuu:truncate niuu:text-[11px] niuu:text-text-faint">
              keeps no realm yet
            </span>
          )}
        </div>
        <Chip tone={state.pulse ? 'brand' : 'muted'}>
          <StateDot state={state.dot} pulse={state.pulse} size={6} />
          {state.label}
        </Chip>
      </div>

      <div className="niuu:flex niuu:flex-wrap niuu:gap-1.5">
        <Chip tone="muted">{ravn.model}</Chip>
        {pendingCount > 0 ? (
          <span
            className="niuu:rounded-full niuu:border niuu:border-status-amber/50 niuu:px-2 niuu:py-0.5 niuu:text-[11px] niuu:text-status-amber"
            data-testid="resident-needs-you"
          >
            {pendingCount} need you
          </span>
        ) : (
          <Chip tone="muted">nothing waiting</Chip>
        )}
      </div>

      <p className="niuu:min-h-8 niuu:text-xs niuu:text-text-muted">
        {activity ? (
          <>
            {activity.label}
            {activity.at ? ` · ${relTime(activity.at)}` : ''}
          </>
        ) : (
          <span className="niuu:text-text-faint">nothing on record yet</span>
        )}
      </p>

      {lifecycle.isError ? (
        <p className="niuu:text-[11px] niuu:text-critical-fg" role="alert">
          {errorMessage(lifecycle.error, 'Resident command failed')}
        </p>
      ) : null}
      {createSession.isError ? (
        <p className="niuu:text-[11px] niuu:text-critical-fg" role="alert">
          {errorMessage(createSession.error, 'Could not open a session')}
        </p>
      ) : null}

      <div className="niuu:flex niuu:items-center niuu:gap-3 niuu:border-t niuu:border-border-subtle niuu:pt-2.5">
        <button
          type="button"
          className={FOOTER_ACTION}
          onClick={() => void talk()}
          disabled={!canTalk || createSession.isPending}
          title={canTalk ? `Talk to ${name}` : 'This resident has no session surface'}
          data-testid="resident-talk"
        >
          <MessageSquare size={13} aria-hidden="true" />
          {createSession.isPending ? 'Opening…' : 'Talk'}
        </button>

        {canSuspendResident(ravn) ? (
          <button
            type="button"
            className={FOOTER_ACTION}
            onClick={() => lifecycle.mutate({ ravn, action: 'suspend' })}
            disabled={lifecycle.isPending}
            data-testid="resident-pause"
          >
            <Pause size={13} aria-hidden="true" />
            Pause
          </button>
        ) : null}

        {canResumeResident(ravn) ? (
          <button
            type="button"
            className={FOOTER_ACTION}
            onClick={() => lifecycle.mutate({ ravn, action: 'resume' })}
            disabled={lifecycle.isPending}
            data-testid="resident-wake"
          >
            <Play size={13} aria-hidden="true" />
            Wake
          </button>
        ) : null}

        {canRestartResident(ravn) ? (
          <button
            type="button"
            className={FOOTER_ACTION}
            onClick={() => lifecycle.mutate({ ravn, action: 'restart' })}
            disabled={lifecycle.isPending}
            data-testid="resident-restart"
          >
            <RotateCw size={13} aria-hidden="true" />
            Restart
          </button>
        ) : null}

        {realm ? (
          <Link
            to={`/realms/${realm.slug}`}
            className="niuu:ml-auto niuu:text-[11px] niuu:text-brand-300"
            data-testid="resident-open-realm"
          >
            Open realm ›
          </Link>
        ) : null}
      </div>
    </article>
  );
}

interface PersonaRowProps {
  persona: PersonaSummary;
  usedBy: number;
  onEdit: (name: string) => void;
}

function PersonaRow({ persona, usedBy, onEdit }: PersonaRowProps) {
  return (
    <div
      className="niuu:flex niuu:items-center niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:py-2.5"
      data-testid={`persona-row-${persona.name}`}
    >
      <PersonaAvatar role={persona.role} letter={persona.letter} size={24} />
      <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
        <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">{persona.name}</span>
        <span className="niuu:truncate niuu:text-xs niuu:text-text-muted">{persona.summary}</span>
      </div>
      <Chip tone="muted">{persona.role}</Chip>
      <span className="niuu:w-32 niuu:text-right niuu:text-[11px] niuu:text-text-muted">
        used by {usedBy} {usedBy === 1 ? 'resident' : 'residents'}
      </span>
      <button type="button" className={BUTTON} onClick={() => onEdit(persona.name)}>
        Edit
      </button>
    </div>
  );
}

export function ResidentsPage() {
  const ravens = useRavens();
  const personas = usePersonas();
  const sessions = useSessions();
  const dashboard = useOptionalValkyrieDashboard();
  const reviews = useOptionalPendingReviews();
  const realms = useOptionalRealms();
  const navigate = useNavigate();
  const [deployOpen, setDeployOpen] = useState(false);

  const residents = useMemo(
    () => (ravens.data ?? []).filter((ravn) => isResidentRavn(ravn)),
    [ravens.data],
  );

  const cards = useMemo(() => {
    const valkyries = dashboard.data?.valkyries ?? [];
    const actions = dashboard.data?.actions ?? [];
    const pending = reviews.data ?? [];
    const realmList = realms.data ?? [];
    const sessionList = sessions.data ?? [];

    const rows = residents.map((ravn) => {
      const valkyrie = valkyrieForRavn(valkyries, ravn);
      const latestSession =
        sessionList
          .filter(
            (session) =>
              session.ravnId === ravn.id &&
              (!ravn.instanceId || session.instanceId === ravn.instanceId),
          )
          .sort((left, right) => right.createdAt.localeCompare(left.createdAt))[0] ?? null;
      return {
        ravn,
        valkyrie,
        realm: realmForRavn(realmList, ravn),
        pendingCount: pendingReviewsFor(pending, valkyrie).length,
        activity: residentActivity(ravn, valkyrie, latestActionFor(actions, valkyrie)),
        session: latestSession,
      };
    });

    const rowsByKey = new Map(rows.map((row) => [ravnKey(row.ravn), row]));
    const ordered = orderResidents(
      residents,
      (ravn) => rowsByKey.get(ravnKey(ravn))?.pendingCount ?? 0,
    );
    return ordered.flatMap((ravn) => {
      const row = rowsByKey.get(ravnKey(ravn));
      return row ? [row] : [];
    });
  }, [residents, dashboard.data, reviews.data, realms.data, sessions.data]);

  function editPersona(name: string) {
    saveStorage(PERSONA_STORAGE_KEY, name);
    window.dispatchEvent(new CustomEvent(PERSONA_SELECTED_EVENT, { detail: name }));
    void navigate({ to: '/ravn/personas' });
  }

  return (
    <div
      className="niuu:flex niuu:flex-col niuu:gap-5 niuu:px-6 niuu:py-5"
      data-testid="residents-page"
    >
      <header className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-6">
        <div className="niuu:flex niuu:max-w-2xl niuu:flex-col niuu:gap-1">
          <h1 className="niuu:text-xl niuu:font-medium niuu:text-text-primary">Residents</h1>
          <p className="niuu:text-xs niuu:leading-relaxed niuu:text-text-muted">
            {LEDE_FIRST}
            <br />
            {LEDE_SECOND}
          </p>
        </div>
        <button
          type="button"
          className={PRIMARY}
          onClick={() => setDeployOpen(true)}
          data-testid="resident-deploy-open"
        >
          <Plus size={13} aria-hidden="true" />
          New resident
        </button>
      </header>

      {ravens.isLoading ? <LoadingState label="Loading residents…" /> : null}

      {ravens.isError ? (
        <ErrorState message={errorMessage(ravens.error, 'Failed to load residents')} />
      ) : null}

      {!ravens.isLoading && !ravens.isError && cards.length === 0 ? (
        <EmptyState
          icon={<Bot size={18} aria-hidden="true" />}
          title="No residents yet"
          description="A resident keeps a realm for you. Deploy one and give it something to keep."
          action={
            <button type="button" className={PRIMARY} onClick={() => setDeployOpen(true)}>
              <Plus size={13} aria-hidden="true" />
              New resident
            </button>
          }
        />
      ) : null}

      {cards.length > 0 ? (
        <div className="niuu:grid niuu:grid-cols-3 niuu:gap-4" data-testid="resident-cards">
          {cards.map((card) => (
            <ResidentCard
              key={ravnKey(card.ravn)}
              ravn={card.ravn}
              realm={card.realm}
              valkyrie={card.valkyrie}
              pendingCount={card.pendingCount}
              activity={card.activity}
              session={card.session}
            />
          ))}
        </div>
      ) : null}

      <section className={CARD} data-testid="personas-section">
        <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-4">
          <div className="niuu:flex niuu:flex-col">
            <h2 className="niuu:text-sm niuu:font-medium niuu:text-text-primary">Personas</h2>
            <p className="niuu:text-[11px] niuu:text-text-muted">
              the character sheet a resident runs with: what it knows, how it behaves
            </p>
          </div>
          <Link to="/ravn/personas" className={LINK} data-testid="persona-new">
            + New persona
          </Link>
        </div>

        {personas.isLoading ? <LoadingState label="Loading personas…" /> : null}
        {personas.isError ? (
          <ErrorState message={errorMessage(personas.error, 'Failed to load personas')} />
        ) : null}
        {!personas.isLoading && !personas.isError && (personas.data ?? []).length === 0 ? (
          <p className="niuu:text-xs niuu:text-text-faint">No personas yet.</p>
        ) : null}

        {(personas.data ?? []).map((persona) => (
          <PersonaRow
            key={persona.name}
            persona={persona}
            usedBy={personaUsage(residents, persona.name)}
            onEdit={editPersona}
          />
        ))}
      </section>

      {/* The deploy hook invalidates ['ravn','ravens'], so the new card arrives on its own. */}
      <ResidentDeployDialog
        open={deployOpen}
        onOpenChange={setDeployOpen}
        onDeployed={() => setDeployOpen(false)}
      />
    </div>
  );
}
