import { useState, type KeyboardEvent } from 'react';
import { Dialog, DialogContent } from '../primitives/Dialog';
import { cn } from '../utils/cn';

/**
 * How well `path` matches `query` as a subsequence, or -1 when it does not. Letters that
 * follow one another and letters in the file's own name count more, so "scope" ranks
 * `token_scope.py` above a folder that merely spells it.
 */
export function fuzzyScore(path: string, query: string): number {
  const needle = query.trim().toLowerCase();
  if (!needle) return 0;
  const haystack = path.toLowerCase();
  const nameStart = haystack.lastIndexOf('/') + 1;
  // The letters typed as one run, in the file's name or anywhere, beat scattered ones.
  let score = haystack.slice(nameStart).includes(needle) ? 20 : haystack.includes(needle) ? 10 : 0;
  let from = 0;
  let previous = -2;
  for (const letter of needle) {
    const at = haystack.indexOf(letter, from);
    if (at < 0) return -1;
    score += 1 + (at === previous + 1 ? 2 : 0) + (at >= nameStart ? 1 : 0);
    previous = at;
    from = at + 1;
  }
  return score;
}

/** Lexi's ⌥⌘O: find a file by a few of its letters; the agent's files come first. */
export function QuickOpenFiles({
  open,
  onOpenChange,
  candidates,
  touched = [],
  onPick,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Every path to search, in the order to show them when nothing is typed. */
  candidates: readonly string[];
  touched?: readonly string[];
  onPick: (path: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const touchedSet = new Set(touched);
  const unique = [...new Set(candidates)];
  const matches = query.trim()
    ? unique
        .map((path) => ({ path, score: fuzzyScore(path, query) }))
        .filter((match) => match.score >= 0)
        .sort((a, b) => b.score - a.score)
        .map((match) => match.path)
    : unique;
  const current = Math.min(active, Math.max(0, matches.length - 1));

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActive(Math.min(current + 1, matches.length - 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive(Math.max(current - 1, 0));
    } else if (event.key === 'Enter' && matches[current]) {
      event.preventDefault();
      onPick(matches[current]);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          setQuery('');
          setActive(0);
        }
        onOpenChange(next);
      }}
    >
      <DialogContent title="Quick open" description="Files the agent touched come first.">
        <div className="niuu-quick-open" data-testid="quick-open">
          <input
            type="search"
            className="niuu-quick-open__input"
            aria-label="Find a file"
            placeholder="Find a file"
            value={query}
            autoFocus
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
          />
          {matches.length === 0 ? (
            <p className="niuu-quick-open__empty">No file matches “{query}”.</p>
          ) : (
            <ul className="niuu-quick-open__list" role="listbox" aria-label="Files">
              {matches.map((path, index) => {
                const slash = path.lastIndexOf('/');
                return (
                  <li key={path}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={index === current}
                      className={cn('niuu-quick-open__item', index === current && 'is-active')}
                      onMouseEnter={() => setActive(index)}
                      onClick={() => onPick(path)}
                    >
                      <span className="niuu-quick-open__name">{path.slice(slash + 1)}</span>
                      <span className="niuu-quick-open__dir">
                        {path.slice(0, Math.max(0, slash))}
                      </span>
                      {touchedSet.has(path) ? (
                        <span
                          className="niuu-quick-open__touched"
                          title="The agent touched this file"
                        >
                          ●
                        </span>
                      ) : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
