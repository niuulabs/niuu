/**
 * MemoryExploreView — the navigable Memory scene at `/mimir`: Explore
 * (default) / Focus (a node is focused) / Ask (a question is asked) / Replay
 * (`asOf` is set). Composes the 3D scene (`ui/scene/MemoryScene`) with the
 * panels in this directory.
 *
 * Node ids are opaque, mount-qualified graph ids
 * (`domain/graphIndex.ts#encodeNodeId`), never page paths — every lookup
 * from a node id back to a page goes through `nodeIndex(graph)`.
 *
 * Ask is answered inline, in the scene: `useMemoryAsk` runs the search and
 * verbatim quoting (`domain/quoteFacts.ts`, shared with `AskAnswerCard`) and
 * maps answering pages to graph node ids for the scene's `answers` prop.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { LoadingState, ErrorState, EmptyState } from '@niuulabs/ui';
import { useMemoryUrl } from '../../application/useMemoryUrl';
import { useMemoryGraph } from '../../application/useMemoryGraph';
import { useLiveActivity } from '../../application/useLiveActivity';
import { useQueryStats } from '../../application/useAnalytics';
import { useLint } from '../../application/useLint';
import { useMemoryAsk } from '../../application/useMemoryAsk';
import { useMimirPage } from '../useMimirPages';
import { MemoryScene } from '../scene/MemoryScene';
import type {
  SceneQuestion,
  FocusDepth,
  ColourBy,
  SceneView,
  SceneCameraCommand,
} from '../scene/types';
import { ExplorePanel, AddSourceForm } from './ExplorePanel';
import { FocusPanel } from './FocusPanel';
import { ReplayPanel } from './ReplayPanel';
import { ReplayTimeline } from './ReplayTimeline';
import { ColourByPanel } from './ColourByPanel';
import { AskBox } from './AskBox';
import { AskAnswerCard } from './AskAnswerCard';
import { HowItAnsweredPanel } from './HowItAnsweredPanel';
import { shortestPath } from '../../domain/pathTrace';
import { zeroResultQueries } from '../../domain/analytics';
import { nearestNodeForQuestion } from '../../domain/questionMatch';
import { recentMarkers } from '../../domain/liveMarkers';
import { nodeIndex } from '../../domain/graphIndex';
import {
  earliestFirstSeen,
  endOfReplayDay,
  perDayHistogram,
  daysPerTick,
  REPLAY_TICK_MS,
  type ReplaySpeed,
} from '../../domain/replayHistogram';
import type { KindGroup } from '../../domain/memoryKinds';
import type { MimirGraph } from '../../domain/api-types';
import type { Page } from '../../domain/page';

const MAX_SUGGESTIONS = 3;
const EXPLORE_HINT =
  'drag to orbit · shift-drag to pan · scroll to zoom · click a page to focus · shift-click a second page to trace the path';
const FOCUS_HINT =
  'esc to step back · shift-click another page to trace the path · drag to orbit around this page';

/** A stable empty graph so hooks that need `MimirGraph` can run before the real graph loads. */
const EMPTY_GRAPH: MimirGraph = { nodes: [], edges: [] };

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

export function MemoryExploreView() {
  const navigate = useNavigate();
  const {
    search,
    focusNode,
    setQuestion,
    setAsOf,
    setDepth,
    setMount,
    setView,
    setColour,
    handleEscape,
  } = useMemoryUrl();

  const mountName = search.mount;
  const depth: FocusDepth = search.depth ?? 1;
  const view: SceneView = search.view ?? '3d';
  const colour: ColourBy = search.colour ?? 'type';
  const focusId = search.focus ?? null;
  const question = search.q ?? null;
  const askMode = question !== null;
  const asOf = search.asOf ?? null;
  const replayMode = asOf !== null;

  const graphQuery = useMemoryGraph(mountName);
  const liveActivityQuery = useLiveActivity();
  const queryStatsQuery = useQueryStats();
  const lintQuery = useLint(mountName);

  const graph = graphQuery.data;
  const index = useMemo(() => nodeIndex(graph ?? EMPTY_GRAPH), [graph]);
  const focusedNode = focusId ? index.byId(focusId) : undefined;
  const focusPageQuery = useMimirPage(focusedNode?.path ?? null, focusedNode?.mount);
  const askResult = useMemoryAsk(question ?? '', mountName, graph ?? EMPTY_GRAPH);

  const [hiddenGroups, setHiddenGroups] = useState<Set<KindGroup>>(new Set());
  const [showQuestions, setShowQuestions] = useState(true);
  const [tracedPath, setTracedPath] = useState<string[]>([]);
  const [camera, setCamera] = useState<SceneCameraCommand | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState<ReplaySpeed>(1);
  const [pendingAskFocus, setPendingAskFocus] = useState(false);

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

  /** Node ids of pages flagged with a contradiction (lint L02) — looked up by (mount, path), never by path alone. */
  const disputedIds = useMemo(() => {
    if (!graph) return new Set<string>();
    const ids = new Set<string>();
    for (const issue of lintQuery.issues) {
      if (issue.rule !== 'L02') continue;
      const node = index.byMountPath(issue.mount, issue.page);
      if (node) ids.add(node.id);
    }
    return ids;
  }, [lintQuery.issues, graph, index]);

  const trimmedQuestion = question?.trim() ?? '';

  const questions: SceneQuestion[] = useMemo(() => {
    if (!graph) return [];
    const out: SceneQuestion[] = [];
    const stats = queryStatsQuery.data;
    if (stats) {
      zeroResultQueries(stats).forEach((entry, i) => {
        out.push({
          id: `zero-${i}-${entry.ts}`,
          label: entry.query,
          nearNodeId: nearestNodeForQuestion(entry.query, graph.nodes),
        });
      });
    }
    if (
      askMode &&
      trimmedQuestion.length > 0 &&
      !askResult.isLoading &&
      !askResult.isError &&
      askResult.results.length === 0
    ) {
      out.push({ id: `ask-zero-${trimmedQuestion}`, label: trimmedQuestion, nearNodeId: null });
    }
    return out;
  }, [
    graph,
    queryStatsQuery.data,
    askMode,
    trimmedQuestion,
    askResult.isLoading,
    askResult.isError,
    askResult.results.length,
  ]);

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
    setQuestion(page.title);
  }

  function handleAsk(q: string) {
    setQuestion(q);
  }

  function handleFollowUp() {
    setQuestion(null);
    setPendingAskFocus(true);
  }

  /** Fly the camera to a mount's pages — `?mount=` stays the scoping param realm links use; this never changes it. */
  function handleFlyToMount(mountToFly: string) {
    if (!graph) return;
    const nodeIds = graph.nodes.filter((n) => n.mount === mountToFly).map((n) => n.id);
    setCamera({ kind: 'fly-to', nodeIds, key: Date.now() });
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
          colourBy={colour}
          hiddenGroups={hiddenGroups}
          showQuestions={showQuestions}
          view={view}
          focus={focusId ? { nodeId: focusId, depth } : null}
          answers={askMode ? askResult.answers : []}
          path={tracedPath.length > 0 ? tracedPath : null}
          asOf={asOf === null ? null : endOfReplayDay(asOf)}
          markers={markers}
          questions={questions}
          disputedIds={disputedIds}
          camera={camera}
          onSelectNode={(id, options) => handleSelectNode(id, { shift: options.shift })}
          onBackgroundClick={handleBackgroundClick}
        />
      </div>

      <div className="niuu:absolute niuu:inset-0 niuu:flex niuu:flex-col niuu:p-4 niuu:pointer-events-none niuu:gap-3">
        {/* ── Top row: scope chip + toolbar + as-of ───────────────────── */}
        <div className="niuu:flex niuu:items-start niuu:justify-between niuu:pointer-events-none">
          <div className="niuu:pointer-events-auto">
            {mountName && (
              <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:text-text-secondary">
                <span>Scoped to {mountName}</span>
                <button
                  type="button"
                  onClick={() => setMount(null)}
                  className="niuu:text-brand-300"
                >
                  Show all memory
                </button>
              </div>
            )}
          </div>
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
                action={<AddSourceForm />}
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
                focusId={focusId}
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
            ) : askMode ? (
              <HowItAnsweredPanel
                question={question!}
                mountName={mountName}
                resultCount={askResult.results.length}
                isLoading={askResult.isLoading}
                isError={askResult.isError}
                elapsedSeconds={askResult.elapsedSeconds}
                onFollowUp={handleFollowUp}
              />
            ) : (
              <ExplorePanel
                graph={graph}
                liveActivity={liveActivityQuery.data}
                liveActivityIsError={liveActivityQuery.isError}
                onFocus={focusAndClearPath}
                onFlyToMount={handleFlyToMount}
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

        {/* ── Bottom row: hint + ask bar / answer card / replay timeline ── */}
        <div className="niuu:flex niuu:flex-col niuu:items-center niuu:gap-2 niuu:pointer-events-none">
          <div className="niuu:w-full niuu:flex niuu:justify-start niuu:pointer-events-auto">
            <p className="niuu:text-xs niuu:text-text-muted niuu:m-0">
              {focusId ? FOCUS_HINT : EXPLORE_HINT}
            </p>
          </div>
          {!replayMode && askMode && (
            <div className="niuu:pointer-events-auto niuu:w-full niuu:flex niuu:justify-center">
              <AskAnswerCard
                question={question!}
                quoted={askResult.quoted}
                rankByPath={askResult.rankByPath}
                nodeIdByPath={askResult.nodeIdByPath}
                resultCount={askResult.results.length}
                isLoading={askResult.isLoading}
                isError={askResult.isError}
                onAsk={handleAsk}
                onFocusPage={focusAndClearPath}
              />
            </div>
          )}
          {!replayMode && !askMode && (
            <div className="niuu:pointer-events-auto niuu:w-full niuu:max-w-2xl niuu:flex niuu:flex-col niuu:gap-2">
              <AskBox
                placeholder="Ask what Niuu knows…"
                onAsk={handleAsk}
                autoFocus={pendingAskFocus}
              />
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
