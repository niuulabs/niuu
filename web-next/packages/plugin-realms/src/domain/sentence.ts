import type { ActionClass } from './realm';

/** What one sentence gives us before the resident has read anything. */
export interface SentenceDraft {
  charter: string;
  repo: string | null;
  boardKey: string | null;
  askBefore: ActionClass[];
  name: string | null;
}

const REPO_PATTERN = /\b([a-z0-9][a-z0-9-]{0,38}\/[a-z0-9._-]{1,100})\b/i;
const BOARD_PATTERN = /\b([A-Z][A-Z0-9]{1,7})\b(?=\s+(?:board|tickets|issues|backlog|project))/;
const BOARD_FALLBACK = /\bboard\s+([A-Z][A-Z0-9]{1,7})\b/;

const ASK_PATTERNS: Array<[RegExp, ActionClass]> = [
  [/ask(?:\s+me)?\s+before\s+(?:anything\s+)?deploy/i, 'deploy'],
  [/ask(?:\s+me)?\s+before\s+(?:anything\s+)?merg/i, 'build'],
  [/ask(?:\s+me)?\s+before\s+(?:you\s+)?spend/i, 'spend'],
  [/never\s+(?:touch|change)\s+(?:infra|production|prod|migrations)/i, 'mutate'],
];

/**
 * Regex-only reading of a sentence like
 * "Keep niuulabs/lexi-api shippable, work the LXA board in priority order, and ask me
 * before anything deploys." Everything not found here is asked for on the Launch step.
 */
export function parseSentence(text: string): SentenceDraft {
  const charter = text.trim();
  const repoMatch = charter.match(REPO_PATTERN);
  const repo = repoMatch?.[1]?.replace(/\.git$/, '') ?? null;
  const boardMatch = charter.match(BOARD_PATTERN) ?? charter.match(BOARD_FALLBACK);
  const boardKey = boardMatch?.[1] ?? null;
  const askBefore = ASK_PATTERNS.filter(([pattern]) => pattern.test(charter)).map(
    ([, actionClass]) => actionClass,
  );
  const name = repo ? (repo.split('/')[1]?.replace(/[-_]+/g, ' ') ?? null) : null;
  return { charter, repo, boardKey, askBefore, name };
}
