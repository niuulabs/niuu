/** HudFilters — Everything / People / Decisions / Tickets: which kinds stay lit in the scene. */
import { HUD_FILTERS, type HudFilterId } from '../../domain/hud';
import './MemoryHud.css';

export interface HudFiltersProps {
  value: HudFilterId;
  onChange: (id: HudFilterId) => void;
}

export function HudFilters({ value, onChange }: HudFiltersProps) {
  return (
    <div role="group" aria-label="Show" className="niuu:flex niuu:gap-1.5">
      {HUD_FILTERS.map((filter) => (
        <button
          key={filter.id}
          type="button"
          aria-pressed={value === filter.id}
          onClick={() => onChange(filter.id)}
          className="niuu-hud-chip"
        >
          {filter.label}
        </button>
      ))}
    </div>
  );
}
