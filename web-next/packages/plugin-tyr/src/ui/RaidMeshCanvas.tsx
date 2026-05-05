import { useEffect, useMemo, useRef, useState } from 'react';
import type { Saga, Phase } from '../domain/saga';

// ---------------------------------------------------------------------------
// Constants (no magic numbers in business logic — these are visual tunables)
// ---------------------------------------------------------------------------

const ORBIT_RADIUS = 46;
const CLUSTER_RADIUS = 36;
const HALO_EXTRA = 28;
const HALO_OPACITY = 0.12;
const EDGE_STROKE = 'rgba(147,197,253,0.18)';
const PULSE_FILL_BASE = 'rgba(186,230,253,';
const RAVEN_FILL = 'rgba(125,211,252,0.85)';
const RAVEN_FILL_HOVER = '#bae6fd';
const RAVEN_RADIUS = 3.2;
const RAVEN_RADIUS_HOVER = 5;
const HOVER_RING_RADIUS = 9;
const HOVER_RING_STROKE = 'rgba(186,230,253,0.4)';
const PULSE_RADIUS = 2.5;
const PULSE_MAX = 18;
const PULSE_SPAWN_MS = 260;
const PULSE_SPEED_MIN = 0.01;
const PULSE_SPEED_RANGE = 0.012;
const PULSE_ALPHA_FACTOR = 1.4;
const HIT_RADIUS_RAVEN = 14;
const LABEL_FONT = '500 10px JetBrains Mono, monospace';
// Fallback colors used until CSS custom properties are resolved in setup()
const LABEL_COLOR_PRIMARY_FALLBACK = '#e4e4e7';
const LABEL_COLOR_MUTED_FALLBACK = '#71717a';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface RaidCluster {
  id: string;
  sagaId: string;
  raidId: string;
  raidName: string;
  phaseName: string;
  status: string;
  confidence: number;
  ravens: string[];
}

interface ClusterNode {
  kind: 'cluster';
  cluster: RaidCluster;
  x: number;
  y: number;
  r: number;
}

interface RavenNode {
  kind: 'raven';
  cluster: RaidCluster;
  persona: string;
  x: number;
  y: number;
  r: number;
}

type MeshNode = ClusterNode | RavenNode;
type Edge = [RavenNode, RavenNode, RaidCluster];

interface Pulse {
  edge: Edge;
  t: number;
  speed: number;
}

interface HoverState {
  node: MeshNode;
  x: number;
  y: number;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface RaidMeshCanvasProps {
  sagas: Saga[];
  phases: Phase[][];
  onClickSaga?: (sagaId: string) => void;
  'aria-label'?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function RaidMeshCanvas({
  sagas,
  phases,
  onClickSaga,
  'aria-label': ariaLabel = 'Live raid mesh visualization',
}: RaidMeshCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nodesRef = useRef<MeshNode[]>([]);
  const hoverRef = useRef<HoverState | null>(null);
  const colorsRef = useRef({
    labelPrimary: LABEL_COLOR_PRIMARY_FALLBACK,
    labelMuted: LABEL_COLOR_MUTED_FALLBACK,
  });
  const [hover, setHover] = useState<HoverState | null>(null);

  // Keep hoverRef in sync with state (used inside animation loop)
  useEffect(() => {
    hoverRef.current = hover;
  }, [hover]);

  // Build raid clusters from sagas + phases — memoized to avoid per-render churn
  const clusters = useMemo<RaidCluster[]>(() => {
    const result: RaidCluster[] = [];
    sagas
      .filter((s) => s.status !== 'complete')
      .forEach((s, i) => {
        const sagaPhases = phases[i] ?? [];
        sagaPhases.forEach((ph) => {
          ph.raids
            .filter((r) => r.status === 'running' || r.status === 'review' || r.status === 'queued')
            .forEach((r) => {
              result.push({
                id: `${s.id}/${r.id}`,
                sagaId: s.id,
                raidId: r.id,
                raidName: r.name,
                phaseName: ph.name,
                status: r.status,
                confidence: r.confidence,
                ravens: ['executor', 'reviewer', 'indexer'].slice(
                  0,
                  r.status === 'running' ? 3 : 2,
                ),
              });
            });
        });
      });
    return result;
  }, [sagas, phases]);

  const visibleClusters = useMemo(() => clusters.slice(0, 6), [clusters]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    let rafId: number;
    let pulses: Pulse[] = [];
    let nodes: MeshNode[] = [];
    let edges: Edge[] = [];

    const ro = new ResizeObserver(() => setup());
    ro.observe(canvas);

    function setup() {
      if (!canvas) return;
      const rect = canvas.getBoundingClientRect();
      const dpr = devicePixelRatio;
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      const W = rect.width;
      const H = rect.height;

      // Resolve colors from CSS custom properties so they respond to theme changes
      const style = getComputedStyle(document.documentElement);
      colorsRef.current = {
        labelPrimary:
          style.getPropertyValue('--color-text-primary').trim() || LABEL_COLOR_PRIMARY_FALLBACK,
        labelMuted:
          style.getPropertyValue('--color-text-muted').trim() || LABEL_COLOR_MUTED_FALLBACK,
      };

      nodes = [];
      const count = visibleClusters.length;
      if (count === 0) {
        nodesRef.current = nodes;
        return;
      }

      const cols = Math.min(count, 3);
      const rows = Math.ceil(count / cols);

      visibleClusters.forEach((cluster, i) => {
        const col = i % cols;
        const row = Math.floor(i / cols);
        const cx = (W * (col + 1)) / (cols + 1);
        const cy = (H * (row + 1)) / (rows + 1);

        const clusterNode: ClusterNode = {
          kind: 'cluster',
          cluster,
          x: cx,
          y: cy,
          r: CLUSTER_RADIUS,
        };
        nodes.push(clusterNode);

        cluster.ravens.forEach((persona, k) => {
          const angle = (k / cluster.ravens.length) * Math.PI * 2 - Math.PI / 2;
          const ravenNode: RavenNode = {
            kind: 'raven',
            cluster,
            persona,
            x: cx + Math.cos(angle) * ORBIT_RADIUS,
            y: cy + Math.sin(angle) * ORBIT_RADIUS,
            r: 10,
          };
          nodes.push(ravenNode);
        });
      });

      // Edges: all raven pairs within same cluster
      edges = [];
      visibleClusters.forEach((cluster) => {
        const mine = nodes.filter(
          (n): n is RavenNode => n.kind === 'raven' && n.cluster.id === cluster.id,
        );
        for (let a = 0; a < mine.length; a++) {
          for (let b = a + 1; b < mine.length; b++) {
            edges.push([mine[a]!, mine[b]!, cluster]);
          }
        }
      });

      nodesRef.current = nodes;
    }

    setup();

    // Pulse spawner
    const spawnInterval = setInterval(() => {
      if (edges.length > 0 && pulses.length < PULSE_MAX) {
        const edge = edges[Math.floor(Math.random() * edges.length)]!;
        pulses.push({ edge, t: 0, speed: PULSE_SPEED_MIN + Math.random() * PULSE_SPEED_RANGE });
      }
    }, PULSE_SPAWN_MS);

    const ctx = canvas.getContext('2d')!;

    function draw() {
      if (!canvas) return;
      const rect = canvas.getBoundingClientRect();
      const W = rect.width;
      const H = rect.height;
      const dpr = devicePixelRatio;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);

      const clusterNodes = nodes.filter((n): n is ClusterNode => n.kind === 'cluster');
      const ravenNodes = nodes.filter((n): n is RavenNode => n.kind === 'raven');
      const hoverNow = hoverRef.current;

      // Cluster halos
      clusterNodes.forEach((n) => {
        const grad = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, n.r + HALO_EXTRA);
        grad.addColorStop(0, `rgba(125,211,252,${HALO_OPACITY})`);
        grad.addColorStop(1, 'rgba(125,211,252,0)');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r + HALO_EXTRA, 0, Math.PI * 2);
        ctx.fill();
      });

      // Edges
      ctx.strokeStyle = EDGE_STROKE;
      ctx.lineWidth = 1;
      edges.forEach(([a, b]) => {
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      });

      // Advance + draw pulses
      pulses.forEach((p) => {
        p.t += p.speed;
      });
      pulses = pulses.filter((p) => p.t < 1);
      pulses.forEach((p) => {
        const [a, b] = p.edge;
        const x = a.x + (b.x - a.x) * p.t;
        const y = a.y + (b.y - a.y) * p.t;
        const alpha = Math.max(0, 1 - Math.abs(p.t - 0.5) * PULSE_ALPHA_FACTOR);
        ctx.fillStyle = `${PULSE_FILL_BASE}${alpha})`;
        ctx.beginPath();
        ctx.arc(x, y, PULSE_RADIUS, 0, Math.PI * 2);
        ctx.fill();
      });

      // Raven nodes
      ravenNodes.forEach((n) => {
        const isHover = hoverNow?.node === n;
        ctx.fillStyle = isHover ? RAVEN_FILL_HOVER : RAVEN_FILL;
        ctx.beginPath();
        ctx.arc(n.x, n.y, isHover ? RAVEN_RADIUS_HOVER : RAVEN_RADIUS, 0, Math.PI * 2);
        ctx.fill();
        if (isHover) {
          ctx.strokeStyle = HOVER_RING_STROKE;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.arc(n.x, n.y, HOVER_RING_RADIUS, 0, Math.PI * 2);
          ctx.stroke();
        }
      });

      // Cluster labels
      ctx.font = LABEL_FONT;
      ctx.textAlign = 'center';
      clusterNodes.forEach((n) => {
        ctx.fillStyle = colorsRef.current.labelPrimary;
        ctx.fillText(n.cluster.raidName.slice(0, 12), n.x, n.y + 4);
        ctx.fillStyle = colorsRef.current.labelMuted;
        ctx.fillText(n.cluster.phaseName.toLowerCase().slice(0, 12), n.x, n.y + 18);
      });

      rafId = requestAnimationFrame(draw);
    }

    draw();

    return () => {
      cancelAnimationFrame(rafId);
      clearInterval(spawnInterval);
      ro.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleClusters.map((c) => c.id).join(',')]);

  function handleMouseMove(e: React.MouseEvent<HTMLDivElement>) {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const hit = nodesRef.current.find((n) => {
      const hitR = n.kind === 'cluster' ? n.r : HIT_RADIUS_RAVEN;
      return Math.hypot(n.x - mx, n.y - my) < hitR;
    });
    setHover(hit ? { node: hit, x: mx, y: my } : null);
  }

  function handleMouseLeave() {
    setHover(null);
  }

  function handleClick() {
    if (!hover) return;
    onClickSaga?.(hover.node.cluster.sagaId);
  }

  return (
    <div
      style={{
        position: 'relative',
        width: '100%',
        height: '100%',
        cursor: hover ? 'pointer' : 'default',
      }}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      onClick={handleClick}
    >
      <canvas
        ref={canvasRef}
        aria-label={ariaLabel}
        style={{ width: '100%', height: '100%', background: 'transparent', display: 'block' }}
      />
      {hover?.node.kind === 'raven' && (
        <MeshTooltip x={hover.x} y={hover.y}>
          <div className="niuu-font-mono niuu-text-xs niuu-font-medium niuu-text-text-primary">
            {hover.node.persona}
          </div>
          <div className="niuu-font-mono niuu-text-xs niuu-mt-0.5 niuu-text-text-muted">
            {hover.node.cluster.raidName} · {hover.node.cluster.phaseName}
          </div>
        </MeshTooltip>
      )}
      {hover?.node.kind === 'cluster' && (
        <MeshTooltip x={hover.x} y={hover.y}>
          <div className="niuu-text-xs niuu-font-medium niuu-text-text-primary">
            {hover.node.cluster.raidName}
          </div>
          <div className="niuu-font-mono niuu-text-xs niuu-mt-0.5 niuu-text-text-muted">
            {hover.node.cluster.phaseName} · conf {hover.node.cluster.confidence}%
          </div>
        </MeshTooltip>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// MeshTooltip — shared tooltip shell; dynamic position stays inline
// ---------------------------------------------------------------------------

interface MeshTooltipProps {
  x: number;
  y: number;
  children: React.ReactNode;
}

function MeshTooltip({ x, y, children }: MeshTooltipProps) {
  return (
    <div
      className="niuu-absolute niuu-bg-bg-elevated niuu-border niuu-border-border niuu-rounded niuu-px-2 niuu-py-1 niuu-pointer-events-none niuu-z-10"
      style={{ left: x + 12, top: y + 12 }}
    >
      {children}
    </div>
  );
}
