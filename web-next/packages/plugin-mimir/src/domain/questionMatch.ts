/**
 * Question → nearest node matching — for unanswered questions surfaced by
 * `mounts.getQueryStats()` zero-result entries. Purely word-overlap based.
 */
import type { GraphNode } from './api-types';

const WORD_PATTERN = /[a-z0-9]+/g;

function wordsOf(text: string): Set<string> {
  return new Set(text.toLowerCase().match(WORD_PATTERN) ?? []);
}

/**
 * Find the node whose title shares the most words with `question`.
 * Returns null when there is no overlap with any node's title, or when
 * `nodes` is empty.
 */
export function nearestNodeForQuestion(question: string, nodes: GraphNode[]): string | null {
  const questionWords = wordsOf(question);
  if (questionWords.size === 0) return null;

  let bestId: string | null = null;
  let bestScore = 0;

  for (const node of nodes) {
    const titleWords = wordsOf(node.title);
    let overlap = 0;
    for (const word of questionWords) {
      if (titleWords.has(word)) overlap += 1;
    }
    if (overlap > bestScore) {
      bestScore = overlap;
      bestId = node.id;
    }
  }

  return bestId;
}
