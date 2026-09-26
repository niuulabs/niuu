/**
 * URL state for the `/mimir` Memory Explore view. Wraps TanStack Router's
 * `useSearch`/`useNavigate` so panels only deal with plain getters/setters,
 * and implements the Escape-key step-back order via `domain/escapeStep.ts`.
 */
import { useCallback } from 'react';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { escapeStep } from '../domain/escapeStep';
import type { MemoryViewSearch } from './memoryViewSearch';

export interface UseMemoryUrlReturn {
  search: MemoryViewSearch;
  patch: (next: Partial<MemoryViewSearch>) => void;
  focusNode: (id: string | null) => void;
  setQuestion: (q: string | null) => void;
  setAsOf: (asOf: string | null) => void;
  setDepth: (depth: 1 | 2 | 3) => void;
  setMount: (mount: string | null) => void;
  setView: (view: '3d' | '2d') => void;
  setColour: (colour: 'type' | 'proof' | 'age') => void;
  /**
   * Apply one Escape step (clears a traced path, then focus, then the
   * question, then exits replay). `tracedPath` is scene-local state, not
   * URL state.
   */
  handleEscape: (tracedPath: string[], clearTracedPath: () => void) => void;
}

export function useMemoryUrl(): UseMemoryUrlReturn {
  const search = useSearch({ strict: false }) as MemoryViewSearch;
  const navigate = useNavigate();

  const patch = useCallback(
    (next: Partial<MemoryViewSearch>) => {
      navigate({
        to: '/mimir',
        search: (prev: Record<string, unknown>) => {
          const merged: Record<string, unknown> = { ...prev, ...next };
          for (const key of Object.keys(merged)) {
            if (merged[key] === null || merged[key] === undefined) delete merged[key];
          }
          return merged;
        },
        replace: false,
      });
    },
    [navigate],
  );

  return {
    search,
    patch,
    focusNode: (id) => patch({ focus: id ?? undefined }),
    setQuestion: (q) => patch({ q: q ?? undefined }),
    setAsOf: (asOf) => patch({ asOf: asOf ?? undefined }),
    setDepth: (depth) => patch({ depth }),
    setMount: (mount) => patch({ mount: mount ?? undefined }),
    setView: (view) => patch({ view }),
    setColour: (colour) => patch({ colour }),
    handleEscape: (tracedPath, clearTracedPath) => {
      const step = escapeStep({
        path: tracedPath,
        focus: search.focus ?? null,
        q: search.q ?? null,
        asOf: search.asOf ?? null,
      });
      if (step === 'path') {
        clearTracedPath();
        return;
      }
      if (step === 'focus') {
        patch({ focus: undefined });
        return;
      }
      if (step === 'q') {
        patch({ q: undefined });
        return;
      }
      if (step === 'asOf') {
        patch({ asOf: undefined });
      }
    },
  };
}
