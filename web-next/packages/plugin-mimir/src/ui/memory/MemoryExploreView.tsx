/**
 * MemoryExploreView — the memory knowledge graph at `/mimir`, as a
 * search-first heads-up display over the 3D scene (`ui/scene/MemoryScene`).
 *
 * Type a name and the camera flies to it: the node sits glowing at the
 * centre with its rings and reticle, its own links carry their relation,
 * what changed this week glows amber, and everything else recedes into a dim
 * cloud you can still orbit. A glass card describes the focused item; the
 * scrubber along the bottom replays how memory looked on an earlier day;
 * Everything / People / Decisions / Tickets keeps one kind lit.
 *
 * The graph is `GET /memory/graph` (`useMemoryGraph`): every layer the
 * signed-in person can read. A name that matches nothing is asked as a
 * question instead (Ask, `?q=`), answered inline by `AskAnswerCard`.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { LoadingState, ErrorState, EmptyState } from '@niuulabs/ui';
import { useMemoryUrl } from '../../application/useMemoryUrl';
import { useMemoryGraph } from '../../application/useMemoryGraph';
import { useMemoryAsk } from '../../application/useMemoryAsk';
import { MemoryScene } from '../scene/MemoryScene';
import type { FocusDepth, SceneView, SceneCameraCommand } from '../scene/types';
import { AddSourceForm } from './ExplorePanel';
import { AskAnswerCard } from './AskAnswerCard';
import { GraphSearch } from './GraphSearch';
import { HudCard } from './HudCard';
import { HudFilters } from './HudFilters';
import { ReplayTimeline } from './ReplayTimeline';
import { shortestPath } from '../../domain/pathTrace';
import { nodeIndex } from '../../domain/graphIndex';
import { neighborsWithinDepth } from '../../domain/graphNeighbors';
import {
  filterKinds,
  neighbourhood,
  recentChangeLine,
  replayMarks,
  type HudFilterId,
} from '../../domain/hud';
import {
  earliestFirstSeen,
  endOfReplayDay,
  perDayHistogram,
  daysPerTick,
  REPLAY_TICK_MS,
  type ReplaySpeed,
} from '../../domain/replayHistogram';
import type { KindGroup } from '../../domain/memoryKinds';
import type { KnowledgeGraph } from '../../domain/api-types';
import './MemoryHud.css';

const HINT = 'drag to orbit · click to fly · type to search';

/** A stable empty graph so hooks that need one can run before the real graph loads. */
const EMPTY_GRAPH: KnowledgeGraph = { nodes: [], edges: [], layers: [], unresolved: [] };

/** Legend groups are not switched off in the heads-up display; the kind filter does that job. */
const NO_HIDDEN_GROUPS: ReadonlySet<KindGroup> = new Set();

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function toFilterId(show: 'people' | 'decisions' | 'tickets' | undefined): HudFilterId {
  return show ?? 'all';
}

function plural(count: number, one: string, many: string): string {
  return `${count.toLocaleString()} ${count === 1 ? one : many}`;
}

/** The traced path's outcome, kept so the status line can say "no path" as well as "path". */
interface TraceResult {
  from: string;
  to: string;
  path: string[];
}

export function MemoryExploreView() {
  const navigate = useNavigate();
  const { search, focusNode, setQuestion, setAsOf, setView, setShow, handleEscape } =
    useMemoryUrl();

  const mountName = search.mount;
  const depth: FocusDepth = search.depth ?? 1;
  const view: SceneView = search.view ?? '3d';
  const filter = toFilterId(search.show);
  const focusId = search.focus ?? null;
  const question = search.q ?? null;
  const askMode = question !== null;
  const asOf = search.asOf ?? null;
  const replayMode = asOf !== null;

  const graphQuery = useMemoryGraph();
  const graph = graphQuery.data;
  const index = useMemo(() => nodeIndex(graph ?? EMPTY_GRAPH), [graph]);
  const focusedNode = focusId ? index.byId(focusId) : undefined;
  const askResult = useMemoryAsk(question ?? '', mountName, graph ?? EMPTY_GRAPH);

  const layerNames = useMemo(
    () => new Map((graph?.layers ?? []).map((l) => [l.id, l.name])),
    [graph],
  );
  const kinds = useMemo(() => filterKinds(filter), [filter]);

  const [trace, setTrace] = useState<TraceResult | null>(null);
  const [tracing, setTracing] = useState(false);
  const [camera, setCamera] = useState<SceneCameraCommand | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState<ReplaySpeed>(1);

  const tracedPath = useMemo(() => trace?.path ?? [], [trace]);

  const clearTrace = useCallback(() => {
    setTrace(null);
    setTracing(false);
  }, []);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== 'Escape') return;
      handleEscape(tracedPath, clearTrace);
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [handleEscape, tracedPath, clearTrace]);

  /** Focus a node (or clear focus with null), always dropping any traced path — it only makes sense from the previous focus. */
  const focusAndClearPath = useCallback(
    (id: string | null) => {
      clearTrace();
      focusNode(id);
    },
    [focusNode, clearTrace],
  );

  const traceTo = useCallback(
    (from: string, to: string) => {
      if (!graph) return;
      setTracing(false);
      setTrace({ from, to, path: shortestPath(graph, from, to) });
    },
    [graph],
  );

  const handleSelectNode = useCallback(
    (id: string, options?: { shift?: boolean }) => {
      if (options?.shift && focusId) {
        traceTo(focusId, id);
        return;
      }
      focusAndClearPath(id);
    },
    [focusId, focusAndClearPath, traceTo],
  );

  /** Search picked a node: fly to it (re-fly when it is already the focus), or end a trace there. */
  function handleSearchSelect(id: string) {
    if (tracing && focusId) {
      traceTo(focusId, id);
      return;
    }
    if (id === focusId && graph) {
      const around = neighborsWithinDepth(graph, id, depth).map((link) => link.node.id);
      setCamera({ kind: 'fly-to', nodeIds: [id, ...around], centre: id, key: Date.now() });
      return;
    }
    focusAndClearPath(id);
  }

  useEffect(() => {
    if (!replayMode || !isPlaying || !graph) return;
    const today = new Date().toISOString().slice(0, 10);
    const id = window.setInterval(() => {
      const cursor = new Date(`${asOf}T00:00:00Z`);
      cursor.setUTCDate(cursor.getUTCDate() + daysPerTick(speed));
      const next = cursor.toISOString().slice(0, 10);
      if (next >= today) {
        setAsOf(null);
        setIsPlaying(false);
        return;
      }
      setAsOf(next);
    }, REPLAY_TICK_MS);
    return () => window.clearInterval(id);
  }, [replayMode, isPlaying, graph, asOf, speed, setAsOf]);

  const timeline = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10);
    if (!graph) return { today, histogram: [] };
    const earliest = earliestFirstSeen(graph.nodes.filter((n) => n.firstSeen));
    return {
      today,
      histogram: earliest ? perDayHistogram(graph.nodes, earliest, today) : [],
    };
  }, [graph]);

  /** The moment the scene shows: the end of the replayed day, or now. */
  const refMs = useMemo(
    () => Date.parse(endOfReplayDay(asOf ?? timeline.today)),
    [asOf, timeline.today],
  );

  const marks = useMemo(
    () => (graph && focusId ? replayMarks(graph, focusId, refMs) : []),
    [graph, focusId, refMs],
  );
  const changeLine = useMemo(
    () => (graph && focusId ? recentChangeLine(graph, focusId, refMs) : null),
    [graph, focusId, refMs],
  );

  const status = useMemo(() => {
    if (!graph) return '';
    const title = (id: string) => index.byId(id)?.title ?? id;
    if (tracing && focusedNode) return `Pick where a path from ${focusedNode.title} should go`;
    if (trace) {
      if (trace.path.length === 0) return `No path from ${title(trace.from)} to ${title(trace.to)}`;
      return `Path · ${trace.path.map(title).join(' → ')} · ${plural(trace.path.length - 1, 'hop', 'hops')}`;
    }
    if (focusedNode) {
      const around = neighbourhood(graph, focusedNode.id);
      return `Flew to ${focusedNode.title} · ${plural(around.connections, 'connection', 'connections')} · ${around.nearby.toLocaleString()} more nearby`;
    }
    if (focusId) return 'That item is not in the memory you can read';
    return `${plural(graph.nodes.length, 'item', 'items')} across ${plural(graph.layers.length, 'layer', 'layers')} · type a name to fly there`;
  }, [graph, index, tracing, trace, focusedNode, focusId]);

  function handleOpenPage() {
    if (!focusedNode) return;
    void navigate({
      to: '/mimir/read',
      search: { path: focusedNode.path, mount: focusedNode.layer ?? focusedNode.mount },
    });
  }

  if (graphQuery.isLoading) {
    return <LoadingState label="loading memory…" />;
  }

  if (graphQuery.isError || !graph) {
    return <ErrorState message={errorMessage(graphQuery.error, 'Memory could not be loaded.')} />;
  }

  const isEmpty = graph.nodes.length === 0;

  return (
    <div
      className="niuu:relative niuu:w-full niuu:h-full niuu:min-h-[600px] niuu:overflow-hidden"
      data-testid="memory-explore-view"
    >
      <div className="niuu:absolute niuu:inset-0">
        <MemoryScene
          graph={graph}
          colourBy="hud"
          hiddenGroups={NO_HIDDEN_GROUPS}
          kinds={kinds}
          layerNames={layerNames}
          showQuestions={false}
          view={view}
          focus={focusId ? { nodeId: focusId, depth } : null}
          answers={askMode ? askResult.answers : []}
          path={tracedPath.length > 0 ? tracedPath : null}
          asOf={asOf === null ? null : endOfReplayDay(asOf)}
          markers={[]}
          questions={[]}
          camera={camera}
          onSelectNode={(id, options) => handleSelectNode(id, { shift: options.shift })}
          onBackgroundClick={() => focusAndClearPath(null)}
        />
      </div>

      <div className="niuu:absolute niuu:inset-0 niuu:flex niuu:flex-col niuu:px-10 niuu:pt-8 niuu:pb-6 niuu:pointer-events-none niuu:gap-4">
        {/* ── Top: search on the left, filters and scene controls on the right ── */}
        <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-6">
          <div className="niuu:pointer-events-auto niuu:w-full niuu:max-w-xl">
            <GraphSearch
              // A fresh box for each mode: a trace never starts from the name just flown to.
              key={tracing ? 'trace' : 'find'}
              nodes={graph.nodes}
              layerNames={layerNames}
              mode={tracing ? 'trace' : 'find'}
              traceFrom={focusedNode?.title}
              status={status}
              onSelect={handleSearchSelect}
              onAsk={setQuestion}
              onCancelTrace={() => setTracing(false)}
            />
          </div>
          <div className="niuu:pointer-events-auto niuu:flex niuu:flex-col niuu:items-end niuu:gap-3">
            <HudFilters value={filter} onChange={(id) => setShow(id === 'all' ? null : id)} />
            <div
              role="toolbar"
              aria-label="Scene controls"
              className="niuu:flex niuu:items-center niuu:gap-3"
            >
              <button
                type="button"
                onClick={() => setCamera({ kind: 'fit', key: Date.now() })}
                className="niuu-hud-link niuu-hud-caption"
              >
                Fit
              </button>
              <button
                type="button"
                aria-pressed={view === '3d'}
                onClick={() => setView('3d')}
                className={
                  view === '3d'
                    ? 'niuu-hud-link niuu-hud-caption'
                    : 'niuu-hud-link niuu-hud-caption niuu-hud-caption--muted'
                }
              >
                3D
              </button>
              <button
                type="button"
                aria-pressed={view === '2d'}
                onClick={() => setView('2d')}
                className={
                  view === '2d'
                    ? 'niuu-hud-link niuu-hud-caption'
                    : 'niuu-hud-link niuu-hud-caption niuu-hud-caption--muted'
                }
              >
                2D
              </button>
              {graph.unresolved.length > 0 && (
                <span
                  className="niuu-hud-caption niuu-hud-caption--muted"
                  title="Links in page text that point at no page you can read"
                >
                  {plural(graph.unresolved.length, 'unresolved link', 'unresolved links')}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* ── Middle: the focused item's card on the right ── */}
        <div className="niuu:flex-1 niuu:flex niuu:items-start niuu:justify-end niuu:overflow-hidden">
          {isEmpty && (
            <div className="niuu:pointer-events-auto niuu:mx-auto niuu:self-center">
              <EmptyState
                title="Nothing in memory yet"
                description="Niuu hasn't written anything yet."
                action={<AddSourceForm />}
              />
            </div>
          )}
          {focusedNode && (
            <div className="niuu:pointer-events-auto niuu:mt-8">
              <HudCard
                node={focusedNode}
                layerName={layerNames.get(focusedNode.mount) ?? focusedNode.mount}
                changeLine={changeLine}
                tracing={tracing}
                onOpenPage={handleOpenPage}
                onTracePath={() => {
                  setTrace(null);
                  setTracing((v) => !v);
                }}
              />
            </div>
          )}
        </div>

        {/* ── Bottom: the answer card when asked, the replay scrubber, the hint ── */}
        {askMode && (
          <div className="niuu:pointer-events-auto niuu:w-full niuu:flex niuu:justify-center">
            <AskAnswerCard
              question={question!}
              quoted={askResult.quoted}
              rankByPath={askResult.rankByPath}
              nodeIdByPath={askResult.nodeIdByPath}
              resultCount={askResult.results.length}
              isLoading={askResult.isLoading}
              isError={askResult.isError}
              onAsk={setQuestion}
              onFocusPage={focusAndClearPath}
            />
          </div>
        )}
        <div className="niuu:flex niuu:items-end niuu:gap-8">
          <div className="niuu:pointer-events-auto niuu:flex-1 niuu:max-w-4xl">
            <ReplayTimeline
              histogram={timeline.histogram}
              asOf={asOf}
              today={timeline.today}
              onAsOfChange={(date) => {
                if (date === null) setIsPlaying(false);
                setAsOf(date);
              }}
              isPlaying={isPlaying}
              onTogglePlay={() => setIsPlaying((v) => !v)}
              speed={speed}
              onSpeedChange={setSpeed}
              marks={marks}
            />
          </div>
          <p className="niuu-hud-mono niuu:m-0 niuu:ml-auto niuu:text-right">{HINT}</p>
        </div>
      </div>
    </div>
  );
}
