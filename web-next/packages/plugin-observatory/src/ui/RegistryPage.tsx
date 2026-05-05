import { Rune, StateDot } from '@niuulabs/ui';
import { useRegistry } from '../application/useRegistry';
import { RegistryEditor } from './RegistryEditor';

export function RegistryPage() {
  const { data, isLoading, isError, error } = useRegistry();

  return (
    <div className="niuu-flex niuu-flex-col niuu-h-full">
      {/* Header — always visible */}
      <div className="niuu-flex niuu-items-center niuu-gap-3 niuu-py-4 niuu-px-6 niuu-border-b niuu-border-border-subtle niuu-shrink-0">
        <Rune glyph="ᛞ" size={28} />
        <div>
          <h2 className="niuu-m-0 niuu-text-lg">Registry</h2>
          <p className="niuu-m-0 niuu-text-text-secondary niuu-text-sm">entity type definitions</p>
        </div>
      </div>

      {/* Content */}
      <div className="niuu-flex-1 niuu-overflow-hidden">
        {isLoading && (
          <div className="niuu-p-6 niuu-flex niuu-items-center niuu-gap-2">
            <StateDot state="processing" pulse />
            <span>loading…</span>
          </div>
        )}

        {isError && (
          <div className="niuu-p-6 niuu-flex niuu-items-center niuu-gap-2">
            <StateDot state="failed" />
            <span>{error instanceof Error ? error.message : 'unknown error'}</span>
          </div>
        )}

        {data && <RegistryEditor registry={data} />}
      </div>
    </div>
  );
}
