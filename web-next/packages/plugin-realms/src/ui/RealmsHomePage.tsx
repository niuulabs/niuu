import { useState } from 'react';
import { Link, useNavigate } from '@tanstack/react-router';
import { useDecideReview, reviewKindLabel, type ReviewItem } from '@niuulabs/plugin-valkyrie';
import { SectionCard } from '@niuulabs/plugin-volundr';
import { Chip, EmptyState, ErrorState, LoadingState, Modal } from '@niuulabs/ui';
import { useRealmsHome } from '../application/useRealmsHome';
import { FIRST_REALM_WALKTHROUGH, useWalkthrough } from '../application/useWalkthrough';
import { REALM_TEMPLATES } from '../domain/templates';
import { RealmCard } from './RealmCard';
import { SentenceComposer } from './SentenceComposer';

export type RealmsHomeView = 'all' | 'needs-you' | 'templates';

const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-text-primary';
const PRIMARY =
  'niuu:rounded-md niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-brand-300';

function NeedsYouRow({ item, realmName }: { item: ReviewItem; realmName: string | null }) {
  const decide = useDecideReview();
  const busy = decide.isPending;
  return (
    <div
      className="niuu:flex niuu:items-center niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:py-2.5"
      data-testid={`needs-you-${item.itemId}`}
    >
      <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
        <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">{item.title}</span>
        <span className="niuu:truncate niuu:text-xs niuu:text-text-muted">
          {realmName ? `${realmName} · ` : ''}
          {item.summary}
        </span>
      </div>
      <Chip tone="muted">{reviewKindLabel(item.kind)}</Chip>
      <button
        type="button"
        className={PRIMARY}
        disabled={busy}
        onClick={() => decide.mutate({ itemId: item.itemId, decision: 'approved' })}
      >
        Approve
      </button>
      <button
        type="button"
        className={BUTTON}
        disabled={busy}
        onClick={() => decide.mutate({ itemId: item.itemId, decision: 'rejected' })}
      >
        Reject
      </button>
      {decide.error ? (
        <span className="niuu:text-xs niuu:text-critical-fg">{String(decide.error)}</span>
      ) : null}
    </div>
  );
}

function TemplatesView() {
  return (
    <div className="niuu:grid niuu:grid-cols-2 niuu:gap-4">
      {REALM_TEMPLATES.map((template) => (
        <SectionCard key={template.id} title={template.name} description={template.blurb}>
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
      ))}
    </div>
  );
}

export function RealmsHomePage({ view = 'all' }: { view?: RealmsHomeView }) {
  const navigate = useNavigate();
  const home = useRealmsHome();
  const walkthrough = useWalkthrough(FIRST_REALM_WALKTHROUGH);
  const [cloneOpen, setCloneOpen] = useState(false);

  const realmNameFor = (environmentId: string) =>
    home.cards.find((card) => card.resident?.environmentId === environmentId)?.realm.name ?? null;

  return (
    <div className="niuu:flex niuu:h-full niuu:flex-col niuu:gap-6 niuu:overflow-auto niuu:p-8" data-testid="realms-home">
      <header className="niuu:flex niuu:items-end niuu:justify-between niuu:gap-6">
        <div className="niuu:flex niuu:max-w-3xl niuu:flex-col niuu:gap-1.5">
          <span className="niuu:font-mono niuu:text-[11px] niuu:uppercase niuu:tracking-[0.3em] niuu:text-brand-300">
            realms
          </span>
          <h1 className="niuu:m-0 niuu:text-2xl niuu:font-bold niuu:tracking-tight niuu:text-text-primary">
            {home.cards.length === 0
              ? 'No realms yet. Start with one sentence.'
              : `${home.cards.length} realm${home.cards.length === 1 ? '' : 's'}, each kept by a resident.`}
          </h1>
          <p className="niuu:m-0 niuu:text-[15px] niuu:text-text-secondary">
            A realm is an environment a resident keeps: it reads your tracker, works the tickets in
            sessions, checks quality, watches health and keeps learning. You review what it asks
            you to.
          </p>
        </div>
        <div className="niuu:flex niuu:gap-2">
          <button type="button" className={BUTTON} onClick={() => setCloneOpen(true)}>
            Clone a realm
          </button>
          <Link to="/realms/new" className={BUTTON}>
            Set one up step by step
          </Link>
        </div>
      </header>

      <SentenceComposer />

      {home.error ? (
        <ErrorState title="Could not load realms" message={String(home.error)} />
      ) : home.isLoading ? (
        <LoadingState label="Loading realms…" />
      ) : view === 'templates' ? (
        <TemplatesView />
      ) : view === 'needs-you' ? (
        <SectionCard title="Needs you" description="Every open question from every resident.">
          {home.pendingReviews.length === 0 ? (
            <EmptyState title="Nothing waiting on you" description="Residents ask here when a grant says so." />
          ) : (
            home.pendingReviews.map((item) => (
              <NeedsYouRow key={item.itemId} item={item} realmName={realmNameFor(item.environmentId)} />
            ))
          )}
        </SectionCard>
      ) : (
        <>
          {home.cards.length === 0 ? (
            <EmptyState
              title="No realms yet"
              description="Type a sentence above, or set one up step by step."
              action={
                <Link to="/realms/new" className={PRIMARY}>
                  Set one up step by step
                </Link>
              }
            />
          ) : (
            <div className="niuu:grid niuu:grid-cols-3 niuu:gap-4" data-testid="realm-cards">
              {home.cards.map((card) => (
                <RealmCard
                  key={card.realm.slug}
                  realm={card.realm}
                  resident={card.resident}
                  ravn={card.ravn}
                  pendingReviews={card.pendingReviews}
                  runningSessions={card.runningSessions}
                />
              ))}
            </div>
          )}
          <div className="niuu:grid niuu:grid-cols-[1.5fr_1fr] niuu:gap-4">
            <SectionCard title="Needs you" description={`${home.pendingReviews.length} open`}>
              {home.pendingReviews.length === 0 ? (
                <EmptyState title="Nothing waiting on you" />
              ) : (
                home.pendingReviews
                  .slice(0, 5)
                  .map((item) => (
                    <NeedsYouRow key={item.itemId} item={item} realmName={realmNameFor(item.environmentId)} />
                  ))
              )}
            </SectionCard>
            <SectionCard title="Walkthroughs">
              <div className="niuu:flex niuu:flex-col niuu:gap-2 niuu:text-sm">
                <div className="niuu:flex niuu:items-center niuu:justify-between">
                  <span className={walkthrough.complete ? 'niuu:text-text-secondary niuu:line-through' : 'niuu:text-text-primary'}>
                    {FIRST_REALM_WALKTHROUGH.title}
                  </span>
                  <span className="niuu:font-mono niuu:text-xs niuu:text-text-faint">
                    {walkthrough.done.length} / {FIRST_REALM_WALKTHROUGH.steps.length}
                  </span>
                </div>
                {walkthrough.hidden ? (
                  <button type="button" className={`${BUTTON} niuu:self-start`} onClick={() => walkthrough.setHidden(false)}>
                    Show it again
                  </button>
                ) : null}
              </div>
            </SectionCard>
          </div>
        </>
      )}

      <Modal
        open={cloneOpen}
        onOpenChange={setCloneOpen}
        title="Clone a realm"
        description="Only the blanks change: repository, board, name. Charter, trust, standing jobs and template come from the realm you pick."
      >
        <div className="niuu:flex niuu:flex-col niuu:gap-2" data-testid="clone-picker">
          {home.cards.length === 0 ? (
            <EmptyState title="No realm to clone yet" />
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
                <span className="niuu:ml-2 niuu:font-mono niuu:text-[11px] niuu:text-text-muted">{card.realm.slug}</span>
              </button>
            ))
          )}
        </div>
      </Modal>
    </div>
  );
}
