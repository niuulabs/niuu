import { useState } from 'react';
import { Link, useNavigate, useParams } from '@tanstack/react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import {
  PersonaForm,
  TriggersView,
  type IPersonaStore,
  type IResidentControl,
  type PersonaCreateRequest,
  type Ravn,
  type ResidentLifecycleAction,
} from '@niuulabs/plugin-ravn';
import { ToolBuilderGrantCard, useCreateTrustGrant } from '@niuulabs/plugin-valkyrie';
import { SectionCard } from '@niuulabs/plugin-volundr';
import {
  Chip,
  EmptyState,
  ErrorState,
  LoadingState,
  MountChip,
  SegmentedFilter,
} from '@niuulabs/ui';
import { TEARDOWN_STEPS, useDeleteRealm } from '../application/useDeleteRealm';
import { RAVENS_QUERY_KEY, useRavens } from '../application/useRealmsHome';
import { useRealmView } from '../application/useRealmView';
import { ravnForRealm } from '../domain/join';
import {
  ACTION_CLASSES,
  ACTION_CLASS_COPY,
  TRUST_LEVEL_FOR,
  trustSettingForLevel,
  type ActionClass,
  type TrustSetting,
} from '../domain/realm';

const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-text-primary niuu:disabled:opacity-40';
const DANGER =
  'niuu:rounded-md niuu:border niuu:border-critical-bo niuu:bg-critical-bg niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-critical-fg niuu:disabled:opacity-40';

/**
 * Pause, resume, restart or remove the resident. The realm record itself has no
 * delete route on the platform yet, so removing the resident is how a realm is
 * switched off: nothing runs, the charter and trust stay for when it comes back.
 */
function ResidentControls({ ravn, realmName }: { ravn: Ravn | null; realmName: string }) {
  const residents = useService<IResidentControl>('ravn.residents');
  const queryClient = useQueryClient();
  const [armed, setArmed] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: RAVENS_QUERY_KEY });
  const lifecycle = useMutation({
    mutationFn: (action: ResidentLifecycleAction) => residents.applyLifecycle(ravn as Ravn, action),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => residents.delete(ravn as Ravn),
    onSuccess: () => {
      setArmed(false);
      void refresh();
    },
  });
  if (!ravn) {
    return (
      <EmptyState
        title="No resident is running for this realm"
        description="Start one from the realm page, or clone the realm to deploy a fresh resident."
      />
    );
  }
  const busy = lifecycle.isPending || remove.isPending;
  const error = lifecycle.error ?? remove.error;
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-3" data-testid="resident-controls">
      <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-xs niuu:text-text-secondary">
        <Chip tone={ravn.status === 'active' ? 'brand' : 'muted'}>{ravn.status}</Chip>
        <span className="niuu:font-mono">{ravn.residentName ?? ravn.id}</span>
      </div>
      <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
        <button
          type="button"
          className={BUTTON}
          disabled={busy}
          onClick={() => lifecycle.mutate('suspend')}
        >
          Pause
        </button>
        <button
          type="button"
          className={BUTTON}
          disabled={busy}
          onClick={() => lifecycle.mutate('resume')}
        >
          Resume
        </button>
        <button
          type="button"
          className={BUTTON}
          disabled={busy}
          onClick={() => lifecycle.mutate('restart')}
        >
          Restart
        </button>
        {armed ? (
          <>
            <button
              type="button"
              className={DANGER}
              disabled={busy}
              onClick={() => remove.mutate()}
              data-testid="resident-remove-confirm"
            >
              Yes, remove {realmName}&apos;s resident
            </button>
            <button type="button" className={BUTTON} onClick={() => setArmed(false)}>
              Keep it
            </button>
          </>
        ) : (
          <button
            type="button"
            className={DANGER}
            disabled={busy}
            onClick={() => setArmed(true)}
            data-testid="resident-remove"
          >
            Remove resident
          </button>
        )}
      </div>
      <span className="niuu:text-xs niuu:text-text-muted">
        Pause keeps the resident deployed but idle. Remove tears its deployment down; the realm,
        its charter, trust and memory stay, and the platform has no route to delete the realm
        record itself yet.
      </span>
      {error ? (
        <span className="niuu:text-xs niuu:text-critical-fg">{String(error)}</span>
      ) : null}
    </div>
  );
}

/** Delete the realm: the create recipe backwards, two clicks, fail loud on the step that broke. */
function RealmTeardown({ slug, ravn, realmName }: { slug: string; ravn: Ravn | null; realmName: string }) {
  const navigate = useNavigate();
  const teardown = useDeleteRealm(slug);
  const [armed, setArmed] = useState(false);
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-2" data-testid="realm-teardown">
      <span className="niuu:text-xs niuu:text-text-muted">
        Deleting the realm removes its resident, persona, memory routing rule, trust and
        capabilities. The Mímir instance stays (the platform cannot remove one yet); its pages
        are still under Mímir › Registry.
      </span>
      <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
        {armed ? (
          <>
            <button
              type="button"
              className={DANGER}
              disabled={teardown.running}
              onClick={() =>
                teardown
                  .run(ravn)
                  .then(() => navigate({ to: '/realms' }))
                  .catch(() => undefined)
              }
              data-testid="realm-delete-confirm"
            >
              Yes, delete {realmName}
            </button>
            <button type="button" className={BUTTON} onClick={() => setArmed(false)}>
              Keep it
            </button>
          </>
        ) : (
          <button
            type="button"
            className={DANGER}
            onClick={() => setArmed(true)}
            data-testid="realm-delete"
          >
            Delete realm
          </button>
        )}
      </div>
      {teardown.error ? (
        <span className="niuu:text-xs niuu:text-critical-fg">
          Stopped at “{TEARDOWN_STEPS.find((step) => step.id === teardown.failedStep)?.label}”:{' '}
          {teardown.error}. Nothing after that step was touched.
        </span>
      ) : null}
    </div>
  );
}

const TRUST_OPTIONS: Array<{ value: TrustSetting; label: string }> = [
  { value: 'auto', label: 'on its own' },
  { value: 'ask', label: 'asks first' },
  { value: 'never', label: 'never' },
];

function TrustRung({
  slug,
  actionClass,
  level,
}: {
  slug: string;
  actionClass: ActionClass;
  level: number | null;
}) {
  const create = useCreateTrustGrant(slug);
  const current = trustSettingForLevel(level);
  return (
    <div className="niuu:flex niuu:items-center niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:py-2">
      <span className="niuu:w-16 niuu:font-mono niuu:text-xs niuu:text-text-primary">
        {actionClass}
      </span>
      <span className="niuu:flex-1 niuu:text-xs niuu:text-text-muted">
        {ACTION_CLASS_COPY[actionClass]}
      </span>
      <SegmentedFilter<TrustSetting>
        options={TRUST_OPTIONS.filter((option) => option.value !== 'never' || current === 'never')}
        value={current}
        aria-label={`${actionClass} trust`}
        onChange={(value) => {
          const nextLevel = TRUST_LEVEL_FOR[value];
          if (nextLevel === null || nextLevel === level) return;
          create.mutate({ action_class: actionClass, target: '*', level: nextLevel, limits: {} });
        }}
      />
      {create.error ? (
        <span className="niuu:text-xs niuu:text-critical-fg">{String(create.error)}</span>
      ) : null}
    </div>
  );
}

export function RealmSettingsPage() {
  const { slug } = useParams({ strict: false }) as { slug: string };
  const data = useRealmView(slug);
  const personas = useService<IPersonaStore>('ravn.personas');
  const queryClient = useQueryClient();
  const persona = useQuery({
    queryKey: ['ravn', 'personas', data.view?.personaName ?? ''],
    queryFn: () => personas.getPersona(data.view?.personaName as string),
    enabled: Boolean(data.view?.personaName),
    // A missing persona is an answer, not a blip worth retrying for seven seconds.
    retry: false,
  });
  const save = useMutation({
    mutationFn: (request: PersonaCreateRequest) => personas.updatePersona(request.name, request),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['ravn', 'personas'] }),
  });
  const [section, setSection] = useState<'charter' | 'trust' | 'jobs' | 'memory' | 'resident'>(
    'charter',
  );
  const ravens = useRavens();
  const ravn = ravnForRealm(ravens.data, slug);

  if (data.isLoading) return <LoadingState label="Loading realm…" />;
  if (data.error)
    return <ErrorState title="Could not load the realm" message={String(data.error)} />;
  if (!data.realm || !data.view)
    return <ErrorState title="No such realm" message={`There is no realm called ${slug}.`} />;

  const view = data.view;
  const levelFor = (actionClass: ActionClass) =>
    view.grants.find((grant) => grant.actionClass === actionClass)?.level ?? null;

  return (
    <div
      className="niuu:flex niuu:h-full niuu:flex-col niuu:gap-5 niuu:overflow-auto niuu:p-8"
      data-testid="realm-settings"
    >
      <header className="niuu:flex niuu:items-center niuu:justify-between">
        <div className="niuu:flex niuu:flex-col niuu:gap-1">
          <Link to="/realms/$slug" params={{ slug }} className="niuu:text-xs niuu:text-brand-300">
            ← {data.realm.name}
          </Link>
          <h1 className="niuu:m-0 niuu:text-xl niuu:font-semibold niuu:text-text-primary">
            Settings
          </h1>
        </div>
        <SegmentedFilter
          options={[
            { value: 'charter', label: 'Charter' },
            { value: 'trust', label: 'Trust' },
            { value: 'jobs', label: 'Standing jobs' },
            { value: 'memory', label: 'Memory' },
            { value: 'resident', label: 'Resident' },
          ]}
          value={section}
          onChange={setSection}
          aria-label="Settings section"
        />
      </header>

      {section === 'charter' ? (
        persona.error ? (
          (persona.error as { status?: number }).status === 404 ? (
            <EmptyState
              title={`No persona named ${view.personaName} yet`}
              description="This realm was not set up by the wizard, so it has no charter persona. Clone it to create one, or write the persona under Ravn › Personas."
            />
          ) : (
            <ErrorState message={String(persona.error)} />
          )
        ) : persona.data ? (
          <SectionCard
            title="Charter and persona"
            description="The persona is the resident's seed. Edit the description (your charter) and the prompt; the rest is Advanced-mode territory but editable here too."
          >
            <PersonaForm
              persona={persona.data}
              onSave={(request) => save.mutateAsync(request).then(() => undefined)}
              isSaving={save.isPending}
            />
          </SectionCard>
        ) : (
          <LoadingState label="Loading persona…" />
        )
      ) : null}

      {section === 'trust' ? (
        <div className="niuu:grid niuu:grid-cols-[1fr_360px] niuu:gap-4">
          <SectionCard
            title="What it may do on its own"
            description="A change adds a new grant; the latest one per action counts."
          >
            <div className="niuu:flex niuu:flex-col">
              {ACTION_CLASSES.map((actionClass) => (
                <TrustRung
                  key={actionClass}
                  slug={slug}
                  actionClass={actionClass}
                  level={levelFor(actionClass)}
                />
              ))}
            </div>
          </SectionCard>
          <ToolBuilderGrantCard realm={{ slug: data.realm.slug, name: data.realm.name }} />
        </div>
      ) : null}

      {section === 'jobs' ? (
        <SectionCard
          title="Standing jobs"
          description="Triggers that wake the resident on a schedule or an event."
        >
          <TriggersView personaName={view.personaName} />
        </SectionCard>
      ) : null}

      {section === 'resident' ? (
        <div className="niuu:flex niuu:flex-col niuu:gap-4">
          <SectionCard
            title="Resident"
            description="Pause, resume, restart or remove the resident that keeps this realm."
          >
            <ResidentControls ravn={ravn} realmName={data.realm.name} />
          </SectionCard>
          <SectionCard title="Delete the realm" description="Gone from the list, for good.">
            <RealmTeardown slug={slug} ravn={ravn} realmName={data.realm.name} />
          </SectionCard>
        </div>
      ) : null}

      {section === 'memory' ? (
        <SectionCard
          title="Realm memory"
          description="The Mímir mount created with the realm, and where its writes go."
        >
          <div className="niuu:flex niuu:flex-col niuu:gap-3">
            <div className="niuu:flex niuu:items-center niuu:gap-2">
              {data.mount ? (
                <MountChip name={data.mount.name} role={data.mount.role} />
              ) : (
                <Chip tone="muted">{data.mountName ?? 'no mount'} · not discovered yet</Chip>
              )}
              <Link to={'/mimir/registry' as never} className="niuu:text-xs niuu:text-brand-300">
                Open in Mímir › Registry
              </Link>
            </div>
            <span className="niuu:text-xs niuu:text-text-muted">
              Writes under <code>realms/{slug}/</code> are routed to this mount; the charter lives
              at <code>realms/{slug}/charter.md</code>.
            </span>
          </div>
        </SectionCard>
      ) : null}
    </div>
  );
}
