import { useRealms } from '../application/useRealmGovernance';
import type { RealmSummary } from '../domain';
import { ToolBuilderGrantCard } from './ToolBuilderGrantCard';

const PANEL =
  'niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:rounded-md';
const MUTED = 'niuu:text-text-muted';

function RealmCard({ realm }: { realm: RealmSummary }) {
  return (
    <article
      data-testid="realm-card"
      className={`${PANEL} niuu:flex niuu:flex-col niuu:gap-3 niuu:p-4`}
    >
      <header className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-2">
        <div>
          <h2 className="niuu:text-sm niuu:text-text-primary">{realm.name}</h2>
          <p className={`niuu:text-xs ${MUTED}`}>{realm.slug}</p>
        </div>
        <span className={`niuu:text-xs ${MUTED}`}>{realm.autonomy_profile}</span>
      </header>
      <ToolBuilderGrantCard realm={realm} />
    </article>
  );
}

export function RealmsPage() {
  const { data, isLoading, error } = useRealms();

  if (isLoading) {
    return (
      <div data-testid="realms-loading" className={`niuu:p-6 niuu:text-sm ${MUTED}`}>
        Loading realms…
      </div>
    );
  }
  if (error) {
    return (
      <div
        data-testid="realms-error"
        role="alert"
        className="niuu:m-4 niuu:rounded-md niuu:border niuu:border-solid niuu:border-critical-bo niuu:bg-critical-bg niuu:p-4 niuu:text-sm niuu:text-critical"
      >
        {error instanceof Error ? error.message : 'Unable to load realms'}
      </div>
    );
  }
  const realms = data ?? [];

  return (
    <div
      data-testid="realms-page"
      className="niuu:h-full niuu:min-h-0 niuu:overflow-auto niuu:bg-bg-primary niuu:p-3"
    >
      <h1 className="niuu:mb-1 niuu:text-base niuu:text-text-primary">Realms</h1>
      <p className={`niuu:mb-3 niuu:text-xs ${MUTED}`}>
        Each realm&apos;s build grant pins the Ting workflow its Valkyrie uses to build tools.
      </p>
      {realms.length === 0 ? (
        <div data-testid="realms-empty" className={`${PANEL} niuu:p-6 niuu:text-sm ${MUTED}`}>
          No realms registered yet.
        </div>
      ) : (
        <div className="niuu:grid niuu:grid-cols-1 niuu:gap-3 niuu:md:grid-cols-2 niuu:xl:grid-cols-3">
          {realms.map((realm) => (
            <RealmCard key={realm.id} realm={realm} />
          ))}
        </div>
      )}
    </div>
  );
}
