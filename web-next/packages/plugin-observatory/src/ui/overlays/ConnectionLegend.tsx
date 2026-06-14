import type { EdgeKind } from '../../domain';
import './ConnectionLegend.css';

interface LegendEntry {
  kind: EdgeKind;
  label: string;
}

/** Semantic relationship categories shown by the Observatory canvas. */
const EDGE_LEGEND: LegendEntry[] = [
  { kind: 'solid', label: 'manages' },
  { kind: 'dashed-anim', label: 'routes / signals' },
  { kind: 'dashed-long', label: 'reads / writes' },
  { kind: 'soft', label: 'uses / exposes' },
  { kind: 'run', label: 'run cohesion' },
];

export function ConnectionLegend() {
  return (
    <ul className="obs-conn-legend" aria-label="Connection types">
      {EDGE_LEGEND.map(({ kind, label }) => (
        <li
          key={kind}
          className="obs-conn-legend__item"
          data-kind={kind}
          data-testid={`legend-${kind}`}
        >
          <svg className="obs-conn-legend__line-svg" width={36} height={14} aria-hidden="true">
            <EdgeLine kind={kind} />
          </svg>
          <span className="obs-conn-legend__label">{label}</span>
        </li>
      ))}
    </ul>
  );
}

function EdgeLine({ kind }: { kind: EdgeKind }) {
  const y = 7;
  const base = { x1: 2, y1: y, x2: 34, y2: y };

  if (kind === 'solid') {
    return <line {...base} className="obs-conn-legend__edge--solid" />;
  }

  if (kind === 'dashed-anim') {
    return (
      <line {...base} className="obs-conn-legend__edge--anim">
        <animate
          attributeName="stroke-dashoffset"
          from="0"
          to="-12"
          dur="0.8s"
          repeatCount="indefinite"
        />
      </line>
    );
  }

  if (kind === 'dashed-long') {
    return <line {...base} className="obs-conn-legend__edge--long" />;
  }

  if (kind === 'soft') {
    return <line {...base} className="obs-conn-legend__edge--soft" />;
  }

  // run — dots + line
  return (
    <g>
      <circle cx={8} cy={y} r={3} className="obs-conn-legend__edge--run-node" />
      <circle cx={28} cy={y} r={3} className="obs-conn-legend__edge--run-node" />
      <line x1={11} y1={y} x2={25} y2={y} className="obs-conn-legend__edge--run-line" />
    </g>
  );
}
