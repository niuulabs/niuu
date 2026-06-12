import { useMemo } from 'react';
import { Activity, Moon, Radio, Wrench } from 'lucide-react';
import type { AutonomyMode, ValkyrieResident, WakefulnessState } from '../domain';
import { useUpdateAutonomy, useValkyrieDashboard } from '../application/useValkyrieDashboard';

const PANEL =
  'niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:rounded-md';
const MUTED = 'niuu:text-text-muted';

const AUTONOMY_MODES: AutonomyMode[] = ['guarded', 'autonomous', 'yolo'];

function wakefulnessIcon(state: WakefulnessState) {
  if (state === 'dreaming') return <Moon size={13} aria-hidden="true" />;
  if (state === 'wakeful') return <Activity size={13} aria-hidden="true" />;
  return <Radio size={13} aria-hidden="true" />;
}

function healthClasses(status: ValkyrieResident['status']): string {
  if (status === 'offline') return 'niuu:text-text-muted';
  if (status === 'blocked') return 'niuu:text-critical';
  if (status === 'busy') return 'niuu:text-state-warn';
  return 'niuu:text-state-ok';
}

function FleetCard({
  valkyrie,
  environmentName,
}: {
  valkyrie: ValkyrieResident;
  environmentName: string;
}) {
  const updateAutonomy = useUpdateAutonomy();

  return (
    <article
      data-testid="fleet-card"
      className={`${PANEL} niuu:flex niuu:flex-col niuu:gap-2 niuu:p-4`}
    >
      <header className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-2">
        <div>
          <h2 className="niuu:text-sm niuu:text-text-primary">{valkyrie.name}</h2>
          <p className={`niuu:text-xs ${MUTED}`}>{environmentName}</p>
        </div>
        <span className={`niuu:text-xs ${healthClasses(valkyrie.status)}`}>{valkyrie.status}</span>
      </header>
      <p className={`niuu:line-clamp-2 niuu:text-xs ${MUTED}`}>{valkyrie.specialty}</p>
      <div className={`niuu:flex niuu:items-center niuu:gap-3 niuu:text-xs ${MUTED}`}>
        <span className="niuu:inline-flex niuu:items-center niuu:gap-1">
          {wakefulnessIcon(valkyrie.wakefulness)}
          {valkyrie.wakefulness}
        </span>
        <span className="niuu:inline-flex niuu:items-center niuu:gap-1">
          <Wrench size={13} aria-hidden="true" />
          {valkyrie.toolCount} tools
        </span>
        <span>{Math.round(valkyrie.confidence * 100)}% confidence</span>
      </div>
      <label className={`niuu:flex niuu:items-center niuu:gap-2 niuu:text-xs ${MUTED}`}>
        autonomy
        <select
          aria-label={`Autonomy mode for ${valkyrie.name}`}
          value={valkyrie.autonomyMode}
          disabled={updateAutonomy.isPending}
          onChange={(event) =>
            updateAutonomy.mutate({
              valkyrieId: valkyrie.id,
              mode: event.target.value as AutonomyMode,
              reason: 'Operator change from the fleet view',
              participantId: 'human:operator',
            })
          }
          className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-primary"
        >
          {AUTONOMY_MODES.map((mode) => (
            <option key={mode} value={mode}>
              {mode}
            </option>
          ))}
        </select>
      </label>
      {updateAutonomy.isError ? (
        <p role="alert" className="niuu:text-xs niuu:text-critical">
          {updateAutonomy.error instanceof Error
            ? updateAutonomy.error.message
            : 'Autonomy change failed'}
        </p>
      ) : null}
    </article>
  );
}

export function FleetPage() {
  const { data, isLoading, error } = useValkyrieDashboard();
  const environmentNames = useMemo(() => {
    const names = new Map<string, string>();
    for (const environment of data?.environments ?? []) {
      names.set(environment.id, environment.name);
    }
    return names;
  }, [data]);

  if (isLoading) {
    return (
      <div data-testid="fleet-loading" className={`niuu:p-6 niuu:text-sm ${MUTED}`}>
        Loading fleet…
      </div>
    );
  }
  if (error) {
    return (
      <div
        data-testid="fleet-error"
        role="alert"
        className="niuu:m-4 niuu:rounded-md niuu:border niuu:border-solid niuu:border-critical-bo niuu:bg-critical-bg niuu:p-4 niuu:text-sm niuu:text-critical"
      >
        {error instanceof Error ? error.message : 'Unable to load the fleet'}
      </div>
    );
  }
  const valkyries = data?.valkyries ?? [];

  return (
    <div
      data-testid="fleet-page"
      className="niuu:h-full niuu:min-h-0 niuu:overflow-auto niuu:bg-bg-primary niuu:p-3"
    >
      <h1 className="niuu:mb-3 niuu:text-base niuu:text-text-primary">Fleet</h1>
      {valkyries.length === 0 ? (
        <div data-testid="fleet-empty" className={`${PANEL} niuu:p-6 niuu:text-sm ${MUTED}`}>
          No resident valkyries announced yet.
        </div>
      ) : (
        <div className="niuu:grid niuu:grid-cols-1 niuu:gap-3 niuu:md:grid-cols-2 niuu:xl:grid-cols-3">
          {valkyries.map((valkyrie) => (
            <FleetCard
              key={valkyrie.id}
              valkyrie={valkyrie}
              environmentName={
                environmentNames.get(valkyrie.environmentId) ?? valkyrie.environmentId
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}
