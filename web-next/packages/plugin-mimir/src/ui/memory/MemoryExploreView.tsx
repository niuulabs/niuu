/**
 * MemoryExploreView — the navigable Memory scene at `/mimir`'s Advanced
 * mode: Explore (default) / Focus (a node is focused) / Replay (`asOf` is
 * set). Composes the scene (`ui/scene/MemoryScene`, a stand-in — see its
 * top comment) with the panels in this directory.
 *
 * Asking a question is not embedded in the scene: the bottom ask bar
 * navigates to the existing `/mimir/ask` page (`AskMemoryPage`), which
 * already implements verbatim fact-quoting and proof display — this view
 * does not duplicate that. See the build report for the full rationale.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { LoadingState, ErrorState, EmptyState } from '@niuulabs/ui';
import { useMemoryUrl } from '../../application/useMemoryUrl';
import { useMemoryGraph } from '../../application/useMemoryGraph';
import { useLiveActivity } from '../../application/useLiveActivity';
import { useQueryStats } from '../../application/useAnalytics';
import { useLint } from '../../application/useLint';
import { useMimirMounts } from '../useMimirMounts';
import { useMimirPage } from '../useMimirPages';
import { MemoryScene } from '../scene/MemoryScene';
import type {
  SceneQuestion,
  FocusDepth,
  ColourBy,
  SceneView,
  SceneCameraCommand,
} from '../scene/types';
import { ExplorePanel } from './ExplorePanel';
import { FocusPanel } from './FocusPanel';
import { ReplayPanel } from './ReplayPanel';
import { ReplayTimeline } from './ReplayTimeline';
import { ColourByPanel } from './ColourByPanel';
import { AskBox } from './AskBox';
import { shortestPath } from '../../domain/pathTrace';
import { zeroResultQueries } from '../../domain/analytics';
import { nearestNodeForQuestion } from '../../domain/questionMatch';
import { recentMarkers } from '../../domain/liveMarkers';
import {
  earliestFirstSeen,
  perDayHistogram,
  daysPerTick,
  REPLAY_TICK_MS,
  type ReplaySpeed,
} from '../../domain/replayHistogram';
import type { KindGroup } from '../../domain/memoryKinds';
import type { Page } from '../../domain/page';

const MAX_SUGGESTIONS = 3;
const EXPLORE_HINT =
  'drag to orbit · shift-drag to pan · scroll to zoom · click a page to focus · shift-click a second page to trace the path';
const FOCUS_HINT =
  'esc to step back · shift-click another page to trace the path · drag to orbit around this page';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

export function MemoryExploreView() {
  const navigate = useNavigate();
  const { search, focusNode, setAsOf, setDepth, setMount, setView, setColour, handleEscape } =
    useMemoryUrl();

  const mountName = search.mount;
  const depth: FocusDepth = search.depth ?? 1;
  const view: SceneView = search.view ?? '3d';
  const colour: ColourBy = search.colour ?? 'type';
  const focusId = search.focus ?? null;
  const asOf = search.asOf ?? null;
  const replayMode = asOf !== null;

  const graphQuery = useMemoryGraph(mountName);
  const mountsQuery = useMimirMounts();
  const liveActivityQuery = useLiveActivity();
  const queryStatsQuery = useQueryStats();
  const lintQuery = useLint(mountName);
  const focusPageQuery = useMimirPage(focusId, mountName);

  const [hiddenGroups, setHiddenGroups] = useState<Set<KindGroup>>(new Set());
  const [showQuestions, setShowQuestions] = useState(true);
  const [tracedPath, setTracedPath] = useState<string[]>([]);
  const [camera, setCamera] = useState<SceneCameraCommand | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState<ReplaySpeed>(1);

  const graph = graphQuery.data;

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== 'Escape') return;
      handleEscape(tracedPath, () => setTracedPath([]));
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [handleEscape, tracedPath]);

  /** Focus a node (or clear focus with null), always dropping any traced path — the path only makes sense relative to the previous focus. */
  const focusAndClearPath = useCallback(
    (id: string | null) => {
      setTracedPath([]);
      focusNode(id);
    },
    [focusNode],
  );

  const handleSelectNode = useCallback(
    (id: string, options?: { shift?: boolean }) => {
      if (options?.shift && focusId && graph) {
        setTracedPath(shortestPath(graph, focusId, id));
        return;
      }
      focusAndClearPath(id);
    },
    [focusId, focusAndClearPath, graph],
  );

  const handleBackgroundClick = useCallback(() => {
    focusAndClearPath(null);
  }, [focusAndClearPath]);

  useEffect(() => {
    if (!replayMode || !isPlaying || !graph) return;
    const today = new Date().toISOString().slice(0, 10);
    const id = window.setInterval(() => {
      const cursor = new Date(`${asOf}T00:00:00Z`);
      cursor.setUTCDate(cursor.getUTCDate() + daysPerTick(speed));
      const next = cursor.toISOString().slice(0, 10);
      if (next >= today) {
        setAsOf(today);
        setIsPlaying(false);
        return;
      }
      setAsOf(next);
    }, REPLAY_TICK_MS);
    return () => window.clearInterval(id);
  }, [replayMode, isPlaying, graph, asOf, speed, setAsOf]);

  const questions: SceneQuestion[] = useMemo(() => {
    if (!graph) return [];
    const stats = queryStatsQuery.data;
    return stats
      ? zeroResultQueries(stats).map((entry, i) => ({
          id: `zero-${i}-${entry.ts}`,
          label: entry.query,
          nearNodeId: nearestNodeForQuestion(entry.query, graph.nodes),
        }))
      : [];
  }, [graph, queryStatsQuery.data]);

  const disputedIds = useMemo(
    () =>
      new Set(lintQuery.issues.filter((issue) => issue.rule === 'L01').map((issue) => issue.page)),
    [lintQuery.issues],
  );

  const markers = useMemo(
    () => (graph ? recentMarkers(liveActivityQuery.data ?? [], graph) : []),
    [liveActivityQuery.data, graph],
  );

  const suggestions = useMemo(() => {
    const stats = queryStatsQuery.data;
    if (!stats) return [];
    const seen = new Set<string>();
    const out: string[] = [];
    for (const entry of stats.recent) {
      if (entry.resultCount === 0 || seen.has(entry.query)) continue;
      seen.add(entry.query);
      out.push(entry.query);
      if (out.length >= MAX_SUGGESTIONS) break;
    }
    return out;
  }, [queryStatsQuery.data]);

  const histogram = useMemo(() => {
    if (!graph) return [];
    const earliest = earliestFirstSeen(graph.nodes);
    if (!earliest) return [];
    return perDayHistogram(graph.nodes, earliest, new Date().toISOString().slice(0, 10));
  }, [graph]);

  function handleFit() {
    setCamera({ kind: 'fit', key: Date.now() });
  }

  function handleEnterReplay() {
    if (!graph) return;
    const earliest = earliestFirstSeen(graph.nodes);
    if (earliest) setAsOf(earliest);
  }

  function handleReadPage(page: Page) {
    void navigate({ to: '/mimir/read', search: { path: page.path, mount: page.mounts[0] } });
  }

  function handleAskAbout(page: Page) {
    void navigate({ to: '/mimir/ask', search: { q: page.title, mount: page.mounts[0] } });
  }

  function handleAsk(question: string) {
    void navigate({ to: '/mimir/ask', search: { q: question, mount: mountName } });
  }

  if (graphQuery.isLoading || mountsQuery.isLoading) {
    return <LoadingState label="loading memory…" />;
  }

  if (graphQuery.isError || !graph) {
    return <ErrorState message={errorMessage(graphQuery.error, 'Memory could not be loaded.')} />;
  }

  const mounts = mountsQuery.data ?? [];
  const isEmpty = graph.nodes.length === 0;

  return (
    <div
      className="niuu:relative niuu:w-full niuu:h-full niuu:min-h-[600px] niuu:overflow-hidden"
      data-testid="memory-explore-view"
    >
      <div className="niuu:absolute niuu:inset-0">
        <MemoryScene
          graph={graph}
          colourBy={colour}
          hiddenGroups={hiddenGroups}
          showQuestions={showQuestions}
          view={view}
          focus={focusId ? { nodeId: focusId, depth } : null}
          answers={[]}
          path={tracedPath.length > 0 ? tracedPath : null}
          asOf={asOf}
          markers={markers}
          questions={questions}
          disputedIds={disputedIds}
          camera={camera}
          onSelectNode={(id, options) => handleSelectNode(id, { shift: options.shift })}
          onBackgroundClick={handleBackgroundClick}
        />
      </div>

      <div className="niuu:absolute niuu:inset-0 niuu:flex niuu:flex-col niuu:p-4 niuu:pointer-events-none niuu:gap-3">
        {/* ── Top row: toolbar + as-of ─────────────────────────────── */}
        <div className="niuu:flex niuu:items-start niuu:justify-between niuu:pointer-events-none">
          <div className="niuu:pointer-events-auto" />
          {!replayMode && (
            <div
              className="niuu:pointer-events-auto niuu:flex niuu:items-center niuu:gap-1 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:p-1"
              role="toolbar"
              aria-label="Scene controls"
            >
              <button
                type="button"
                onClick={handleFit}
                className="niuu:px-3 niuu:py-1 niuu:text-xs niuu:rounded-sm niuu:text-text-secondary niuu:hover:bg-bg-tertiary"
              >
                Fit
              </button>
              <button
                type="button"
                onClick={handleEnterReplay}
                className="niuu:px-3 niuu:py-1 niuu:text-xs niuu:rounded-sm niuu:text-text-secondary niuu:hover:bg-bg-tertiary"
              >
                Replay
              </button>
              <div className="niuu:w-px niuu:h-4 niuu:bg-border-subtle niuu:mx-1" aria-hidden />
              <button
                type="button"
                aria-pressed={view === '3d'}
                onClick={() => setView('3d')}
                className={
                  view === '3d'
                    ? 'niuu:px-3 niuu:py-1 niuu:text-xs niuu:rounded-sm niuu:bg-bg-tertiary niuu:text-text-primary'
                    : 'niuu:px-3 niuu:py-1 niuu:text-xs niuu:rounded-sm niuu:text-text-muted'
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
                    ? 'niuu:px-3 niuu:py-1 niuu:text-xs niuu:rounded-sm niuu:bg-bg-tertiary niuu:text-text-primary'
                    : 'niuu:px-3 niuu:py-1 niuu:text-xs niuu:rounded-sm niuu:text-text-muted'
                }
              >
                2D
              </button>
            </div>
          )}
          <div className="niuu:pointer-events-auto niuu:flex niuu:items-center niuu:gap-2 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:px-3 niuu:py-1.5">
            <span className="niuu:text-xs niuu:text-text-secondary">
              {asOf === null ? 'As of now' : `As of ${asOf}`}
            </span>
            <input
              type="date"
              value={asOf ?? ''}
              onChange={(e) => setAsOf(e.target.value || null)}
              aria-label="Replay date"
              className="niuu:bg-transparent niuu:text-xs niuu:text-text-muted niuu:border-0 niuu:w-28"
            />
            {asOf !== null && (
              <button
                type="button"
                onClick={() => setAsOf(null)}
                aria-label="Back to now"
                className="niuu:text-xs niuu:text-brand-300"
              >
                now
              </button>
            )}
          </div>
        </div>

        {/* ── Middle row: left panel + colour-by panel ────────────── */}
        <div className="niuu:flex-1 niuu:flex niuu:items-start niuu:justify-between niuu:overflow-hidden">
          <div className="niuu:pointer-events-auto niuu:h-full niuu:overflow-hidden">
            {isEmpty ? (
              <EmptyState
                title="Nothing in memory yet"
                description="Niuu hasn't written anything yet."
                action={
                  <span className="niuu:text-xs niuu:text-text-muted">
                    Use &quot;Add a source&quot; below to get started.
                  </span>
                }
              />
            ) : replayMode ? (
              <ReplayPanel
                graph={graph}
                asOf={asOf!}
                onExitReplay={() => setAsOf(null)}
                onFocus={focusAndClearPath}
              />
            ) : focusId ? (
              <FocusPanel
                page={focusPageQuery.data ?? null}
                isLoading={focusPageQuery.isLoading}
                isError={focusPageQuery.isError}
                graph={graph}
                depth={depth}
                disputedIds={disputedIds}
                onDepthChange={setDepth}
                onClearFocus={() => focusAndClearPath(null)}
                onFocusPage={focusAndClearPath}
                onReadPage={handleReadPage}
                onAskAbout={handleAskAbout}
              />
            ) : (
              <ExplorePanel
                graph={graph}
                mounts={mounts}
                liveActivity={liveActivityQuery.data}
                liveActivityIsError={liveActivityQuery.isError}
                onFocus={focusAndClearPath}
                onFlyToMount={(m) => {
                  setMount(m);
                  handleFit();
                }}
              />
            )}
          </div>

          <div className="niuu:pointer-events-auto">
            <ColourByPanel
              graph={graph}
              colour={colour}
              onColourChange={setColour}
              hiddenGroups={hiddenGroups}
              onToggleGroup={(id) =>
                setHiddenGroups((prev) => {
                  const next = new Set(prev);
                  if (next.has(id)) next.delete(id);
                  else next.add(id);
                  return next;
                })
              }
              showQuestions={showQuestions}
              onToggleQuestions={() => setShowQuestions((v) => !v)}
              questionCount={questions.length}
            />
          </div>
        </div>

        {/* ── Bottom row: hint + ask bar / replay timeline ────────── */}
        <div className="niuu:flex niuu:flex-col niuu:items-center niuu:gap-2 niuu:pointer-events-none">
          <div className="niuu:w-full niuu:flex niuu:justify-start niuu:pointer-events-auto">
            <p className="niuu:text-xs niuu:text-text-muted niuu:m-0">
              {focusId ? FOCUS_HINT : EXPLORE_HINT}
            </p>
          </div>
          {!replayMode && (
            <div className="niuu:pointer-events-auto niuu:w-full niuu:max-w-2xl niuu:flex niuu:flex-col niuu:gap-2">
              <AskBox placeholder="Ask what Niuu knows…" onAsk={handleAsk} />
              {suggestions.length > 0 && (
                <div className="niuu:flex niuu:gap-2 niuu:flex-wrap niuu:justify-center">
                  {suggestions.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => handleAsk(s)}
                      className="niuu:px-3 niuu:py-1 niuu:text-xs niuu:rounded-full niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:text-text-secondary niuu:hover:text-text-primary"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {replayMode && (
            <div className="niuu:pointer-events-auto niuu:w-full niuu:flex niuu:justify-center">
              <ReplayTimeline
                histogram={histogram}
                asOf={asOf!}
                onAsOfChange={setAsOf}
                isPlaying={isPlaying}
                onTogglePlay={() => setIsPlaying((v) => !v)}
                speed={speed}
                onSpeedChange={setSpeed}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
