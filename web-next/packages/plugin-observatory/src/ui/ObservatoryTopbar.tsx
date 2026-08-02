import { useTopology } from '../application/useTopology';
import { useObservatoryStore } from '../application/useObservatoryStore';
import { ObservatoryReadout } from './ObservatoryReadout';
import './ObservatoryTopbar.css';

/**
 * The Observatory's half of the shell topbar.
 *
 * The shell already puts the plugin's name, subtitle and tabs on the left of
 * this bar, so the page must not draw a header of its own — doing that is how
 * the estate ended up stating its realm count twice, in two shapes, a few
 * pixels apart. Everything the mockup's header carries that the shell does
 * not — the readout, and the way into present mode — belongs here.
 */
export function ObservatoryTopbar() {
  const topology = useTopology();
  const [{ presenting }, store] = useObservatoryStore();

  return (
    <div className="obs-topbar" data-testid="observatory-topbar">
      <ObservatoryReadout topology={topology} />
      <button
        type="button"
        className="obs-topbar__present"
        data-testid="present-toggle"
        aria-pressed={presenting}
        title="Hide the rail, inspector and feed — the graph alone"
        onClick={() => store.setPresenting(!presenting)}
      >
        Present
      </button>
    </div>
  );
}
