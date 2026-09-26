/**
 * Ask mode for the Memory Explore scene — runs the same hybrid search and
 * verbatim-quoting the flat `/mimir/ask` page used (reusing its hooks from
 * `ui/memory/useMemory.ts` and `domain/quoteFacts.ts`, not duplicating
 * them), and additionally maps the answering pages to graph node ids for
 * the scene's `answers` prop.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useMemorySearch, usePagesForPaths, useEvidenceForPaths } from '../ui/memory/useMemory';
import { quoteKeyFacts, type QuotedFact } from '../domain/quoteFacts';
import { nodeIndex } from '../domain/graphIndex';
import type { MimirGraph } from '../domain/api-types';
import type { Page, SearchResult } from '../domain/page';

/** Answers quote from at most this many top-ranked pages. */
export const MAX_ANSWER_PAGES = 3;

export interface AskAnswer {
  nodeId: string;
  /** 1-based, matches the quoted fact's citation number. */
  n: number;
}

export interface UseMemoryAskReturn {
  results: SearchResult[];
  quoted: QuotedFact[];
  answers: AskAnswer[];
  /** 1-based rank (matching `answers[].n`) of the page at each answering path. */
  rankByPath: ReadonlyMap<string, number>;
  /** Graph node id of the page at each answering path, when one resolves. */
  nodeIdByPath: ReadonlyMap<string, string>;
  isLoading: boolean;
  isError: boolean;
  /** Seconds elapsed between the query starting and its results resolving. */
  elapsedSeconds: number | null;
}

function answerNode(
  row: SearchResult,
  mountName: string | undefined,
  index: ReturnType<typeof nodeIndex>,
) {
  const mount = mountName ?? row.mounts?.[0];
  return mount ? index.byMountPath(mount, row.path) : undefined;
}

export function useMemoryAsk(
  question: string,
  mountName: string | undefined,
  graph: MimirGraph,
): UseMemoryAskReturn {
  const trimmed = question.trim();
  const startedAtRef = useRef<number | null>(null);
  const wasLoadingRef = useRef(false);
  const [elapsedSeconds, setElapsedSeconds] = useState<number | null>(null);

  const search = useMemorySearch(trimmed, mountName);
  const rows = useMemo(() => search.data ?? [], [search.data]);
  const topPaths = useMemo(() => rows.slice(0, MAX_ANSWER_PAGES).map((r) => r.path), [rows]);

  const pagesByPath = usePagesForPaths(topPaths, mountName);
  const { byPath: evidenceByPath } = useEvidenceForPaths(topPaths);

  const pages = useMemo<Page[]>(
    () => topPaths.map((p) => pagesByPath.get(p)).filter((p): p is Page => Boolean(p)),
    [topPaths, pagesByPath],
  );

  const quoted = useMemo(() => quoteKeyFacts(pages, evidenceByPath), [pages, evidenceByPath]);

  const index = useMemo(() => nodeIndex(graph), [graph]);
  const answers = useMemo<AskAnswer[]>(() => {
    const out: AskAnswer[] = [];
    rows.slice(0, MAX_ANSWER_PAGES).forEach((row, i) => {
      const node = answerNode(row, mountName, index);
      if (node) out.push({ nodeId: node.id, n: i + 1 });
    });
    return out;
  }, [rows, mountName, index]);

  const rankByPath = useMemo(() => new Map(topPaths.map((path, i) => [path, i + 1])), [topPaths]);

  const nodeIdByPath = useMemo(() => {
    const out = new Map<string, string>();
    for (const row of rows.slice(0, MAX_ANSWER_PAGES)) {
      const node = answerNode(row, mountName, index);
      if (node) out.set(row.path, node.id);
    }
    return out;
  }, [rows, mountName, index]);

  const isLoading = search.isLoading && trimmed.length > 0;

  useEffect(() => {
    if (isLoading && !wasLoadingRef.current) {
      startedAtRef.current = performance.now();
      setElapsedSeconds(null);
    }
    if (!isLoading && wasLoadingRef.current && startedAtRef.current !== null) {
      setElapsedSeconds((performance.now() - startedAtRef.current) / 1000);
      startedAtRef.current = null;
    }
    wasLoadingRef.current = isLoading;
  }, [isLoading]);

  return {
    results: rows,
    quoted,
    answers,
    rankByPath,
    nodeIdByPath,
    isLoading,
    isError: search.isError,
    elapsedSeconds,
  };
}
