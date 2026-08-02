import { useTopology } from '../application/useTopology';
import { useObservatoryStore } from '../application/useObservatoryStore';
import './ObservatorySubnav.css';
import { ObservatoryRailSections } from './ObservatoryRailSections';

/**
 * The Observatory rail, in the shell's subnav slot.
 *
 * Every section lives in `ObservatoryRailSections`; this is only the frame and
 * the scroll. Realms and clusters used to be listed here as well as there,
 * which is how the estate ended up enumerated twice in one column.
 */
export function ObservatorySubnav() {
  const topology = useTopology();
  const [{ selectedId, presenting }, store] = useObservatoryStore();

  // In present mode the rail leaves entirely rather than narrowing: the shell
  // collapses an empty subnav to nothing, which is the effect wanted — a strip
  // of truncated icons is not a presentation.
  if (presenting) return null;

  return (
    <div className="obs-subnav" data-testid="observatory-subnav">
      <ObservatoryRailSections
        topology={topology}
        selectedId={selectedId}
        onSelect={(nodeId, options) => store.setSelected(nodeId, options)}
      />
    </div>
  );
}
