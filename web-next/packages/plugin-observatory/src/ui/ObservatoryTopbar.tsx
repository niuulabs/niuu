import { useTopology } from '../application/useTopology';
import { useObservatoryStore, type ObservatoryView } from '../application/useObservatoryStore';
import { ObservatoryReadout } from './ObservatoryReadout';
import './ObservatoryTopbar.css';

const VIEWS: ReadonlyArray<{ id: ObservatoryView; label: string; title: string }> = [
  { id: '2d', label: '2D', title: 'The estate in plan — pan, zoom, click' },
  { id: '3d', label: '3D', title: 'The estate as a model — orbit, slide, click' },
];

/**
 * The Observatory's half of the shell topbar.
 *
 * The shell already puts the plugin's name, subtitle and tabs on the left of
 * this bar, so the page must not draw a header of its own — doing that is how
 * the estate ended up stating its realm count twice, in two shapes, a few
 * pixels apart. Everything the mockup's header carries that the shell does
 * not — the readout, the choice of stage, and the way into present mode —
 * belongs here.
 */
export function ObservatoryTopbar() {
  const topology = useTopology();
  const [{ presenting, view }, store] = useObservatoryStore();

  return (
    <div className="obs-topbar" data-testid="observatory-topbar">
      <ObservatoryReadout topology={topology} />

      {/*
        A segmented pair rather than a single toggle: which stage you are on is
        a state worth reading off the bar, and a lone "3D" button that lights up
        makes the plan look like the absence of a mode rather than a mode.
      */}
      <div className="obs-topbar__views" role="group" aria-label="Topology view">
        {VIEWS.map(({ id, label, title }) => (
          <button
            key={id}
            type="button"
            className="obs-topbar__view"
            data-testid={`view-toggle-${id}`}
            aria-pressed={view === id}
            title={title}
            onClick={() => store.setView(id)}
          >
            {label}
          </button>
        ))}
      </div>

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
