import { useState } from 'react';
import { Link, useParams } from '@tanstack/react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { PersonaForm, TriggersView, type IPersonaStore, type PersonaCreateRequest } from '@niuulabs/plugin-ravn';
import { ToolBuilderGrantCard, useCreateTrustGrant } from '@niuulabs/plugin-valkyrie';
import { SectionCard } from '@niuulabs/plugin-volundr';
import { Chip, ErrorState, LoadingState, MountChip, SegmentedFilter } from '@niuulabs/ui';
import { useRealmView } from '../application/useRealmView';
import {
  ACTION_CLASSES,
  ACTION_CLASS_COPY,
  TRUST_LEVEL_FOR,
  trustSettingForLevel,
  type ActionClass,
  type TrustSetting,
} from '../domain/realm';

const TRUST_OPTIONS: Array<{ value: TrustSetting; label: string }> = [
  { value: 'auto', label: 'on its own' },
  { value: 'ask', label: 'asks first' },
  { value: 'never', label: 'never' },
];

function TrustRung({ slug, actionClass, level }: { slug: string; actionClass: ActionClass; level: number | null }) {
  const create = useCreateTrustGrant(slug);
  const current = trustSettingForLevel(level);
  return (
    <div className="niuu:flex niuu:items-center niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:py-2">
      <span className="niuu:w-16 niuu:font-mono niuu:text-xs niuu:text-text-primary">{actionClass}</span>
      <span className="niuu:flex-1 niuu:text-xs niuu:text-text-muted">{ACTION_CLASS_COPY[actionClass]}</span>
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
      {create.error ? <span className="niuu:text-xs niuu:text-critical-fg">{String(create.error)}</span> : null}
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
  });
  const save = useMutation({
    mutationFn: (request: PersonaCreateRequest) => personas.updatePersona(request.name, request),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['ravn', 'personas'] }),
  });
  const [section, setSection] = useState<'charter' | 'trust' | 'jobs' | 'memory'>('charter');

  if (data.isLoading) return <LoadingState label="Loading realm…" />;
  if (data.error) return <ErrorState title="Could not load the realm" message={String(data.error)} />;
  if (!data.realm || !data.view) return <ErrorState title="No such realm" message={`There is no realm called ${slug}.`} />;

  const view = data.view;
  const levelFor = (actionClass: ActionClass) =>
    view.grants.find((grant) => grant.actionClass === actionClass)?.level ?? null;

  return (
    <div className="niuu:flex niuu:h-full niuu:flex-col niuu:gap-5 niuu:overflow-auto niuu:p-8" data-testid="realm-settings">
      <header className="niuu:flex niuu:items-center niuu:justify-between">
        <div className="niuu:flex niuu:flex-col niuu:gap-1">
          <Link to="/realms/$slug" params={{ slug }} className="niuu:text-xs niuu:text-brand-300">
            ← {data.realm.name}
          </Link>
          <h1 className="niuu:m-0 niuu:text-xl niuu:font-semibold niuu:text-text-primary">Settings</h1>
        </div>
        <SegmentedFilter
          options={[
            { value: 'charter', label: 'Charter' },
            { value: 'trust', label: 'Trust' },
            { value: 'jobs', label: 'Standing jobs' },
            { value: 'memory', label: 'Memory' },
          ]}
          value={section}
          onChange={setSection}
          aria-label="Settings section"
        />
      </header>

      {section === 'charter' ? (
        persona.error ? (
          <ErrorState message={String(persona.error)} />
        ) : persona.data ? (
          <SectionCard title="Charter and persona" description="The persona is the resident's seed. Edit the description (your charter) and the prompt; the rest is Advanced-mode territory but editable here too.">
            <PersonaForm persona={persona.data} onSave={(request) => save.mutateAsync(request).then(() => undefined)} isSaving={save.isPending} />
          </SectionCard>
        ) : (
          <LoadingState label="Loading persona…" />
        )
      ) : null}

      {section === 'trust' ? (
        <div className="niuu:grid niuu:grid-cols-[1fr_360px] niuu:gap-4">
          <SectionCard title="What it may do on its own" description="A change adds a new grant; the latest one per action counts.">
            <div className="niuu:flex niuu:flex-col">
              {ACTION_CLASSES.map((actionClass) => (
                <TrustRung key={actionClass} slug={slug} actionClass={actionClass} level={levelFor(actionClass)} />
              ))}
            </div>
          </SectionCard>
          <ToolBuilderGrantCard realm={{ slug: data.realm.slug, name: data.realm.name }} />
        </div>
      ) : null}

      {section === 'jobs' ? (
        <SectionCard title="Standing jobs" description="Triggers that wake the resident on a schedule or an event.">
          <TriggersView personaName={view.personaName} />
        </SectionCard>
      ) : null}

      {section === 'memory' ? (
        <SectionCard title="Realm memory" description="The Mímir mount created with the realm, and where its writes go.">
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
              Writes under <code>realms/{slug}/</code> are routed to this mount; the charter lives at{' '}
              <code>realms/{slug}/charter.md</code>.
            </span>
          </div>
        </SectionCard>
      ) : null}
    </div>
  );
}
