import { Chip, StateDot, Rune } from '@niuulabs/ui';
import { useGreetings } from './useGreetings';

export function HelloPage() {
  const { data, isLoading, isError, error } = useGreetings();

  return (
    <div className="niuu-p-6 niuu-max-w-[720px]">
      <div className="niuu-flex niuu-items-center niuu-gap-3 niuu-mb-4">
        <Rune glyph="ᚺ" size={32} />
        <h2 className="niuu-m-0">hello · smoke test</h2>
      </div>
      <p className="niuu-text-text-secondary">
        This plugin proves the end-to-end composability loop: PluginDescriptor → Shell →
        ServicesProvider → TanStack Query → UI.
      </p>

      {isLoading && (
        <div className="niuu-flex niuu-items-center niuu-gap-2" role="status">
          <StateDot state="processing" pulse />
          <span>loading…</span>
        </div>
      )}

      {isError && (
        <div className="niuu-flex niuu-items-center niuu-gap-2" role="alert">
          <StateDot state="failed" />
          <span>{error instanceof Error ? error.message : 'unknown error'}</span>
        </div>
      )}

      {data && (
        <ul className="niuu-list-none niuu-p-0 niuu-grid niuu-gap-3">
          {data.map((g) => (
            <li
              key={g.id}
              className="niuu-flex niuu-items-center niuu-gap-3 niuu-p-3 niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-secondary"
            >
              <StateDot
                state={g.mood === 'warm' ? 'healthy' : g.mood === 'cold' ? 'idle' : 'observing'}
              />
              <span className="niuu-flex-1">{g.text}</span>
              <Chip tone="brand">{g.mood}</Chip>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
