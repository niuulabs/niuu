import { useCallback, useSyncExternalStore } from 'react';

export interface WalkthroughStep {
  id: string;
  title: string;
  detail?: string;
}

export interface Walkthrough {
  id: string;
  title: string;
  tip: string;
  steps: WalkthroughStep[];
}

export const FIRST_REALM_WALKTHROUGH: Walkthrough = {
  id: 'first-realm',
  title: 'Set up your first realm',
  tip: 'You are not managing tickets any more. You are managing one resident, and it manages the tickets.',
  steps: [
    { id: 'template', title: 'Pick a template', detail: 'Product resident fits most codebases.' },
    { id: 'connect', title: 'Connect repo, tracker, CI' },
    {
      id: 'charter',
      title: 'Write the charter',
      detail: 'A few sentences. It is a seed, not a rulebook.',
    },
    { id: 'trust', title: 'Set trust and where it runs' },
    {
      id: 'first-answer',
      title: 'Answer its first question',
      detail: 'Deciding the first review teaches it what you care about.',
    },
  ],
};

const STORAGE_PREFIX = 'niuu.compactUx.walkthrough.';
const EVENT = 'niuu:walkthrough';

function read(id: string): { done: string[]; hidden: boolean } {
  if (typeof window === 'undefined') return { done: [], hidden: false };
  try {
    const raw = window.localStorage.getItem(STORAGE_PREFIX + id);
    if (!raw) return { done: [], hidden: false };
    const parsed = JSON.parse(raw) as { done?: unknown; hidden?: unknown };
    return {
      done: Array.isArray(parsed.done) ? parsed.done.filter((v) => typeof v === 'string') : [],
      hidden: parsed.hidden === true,
    };
  } catch {
    return { done: [], hidden: false };
  }
}

function write(id: string, value: { done: string[]; hidden: boolean }) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_PREFIX + id, JSON.stringify(value));
    window.dispatchEvent(new Event(EVENT));
  } catch {
    // localStorage unavailable; progress is a convenience only
  }
}

function subscribe(listener: () => void) {
  window.addEventListener('storage', listener);
  window.addEventListener(EVENT, listener);
  return () => {
    window.removeEventListener('storage', listener);
    window.removeEventListener(EVENT, listener);
  };
}

/** Per-viewer walkthrough progress, kept in localStorage under the compactUx namespace. */
export function useWalkthrough(walkthrough: Walkthrough) {
  const snapshot = useSyncExternalStore(
    subscribe,
    () => JSON.stringify(read(walkthrough.id)),
    () => JSON.stringify({ done: [], hidden: false }),
  );
  const state = JSON.parse(snapshot) as { done: string[]; hidden: boolean };

  const markDone = useCallback(
    (stepId: string) => {
      const current = read(walkthrough.id);
      if (current.done.includes(stepId)) return;
      write(walkthrough.id, { ...current, done: [...current.done, stepId] });
    },
    [walkthrough.id],
  );

  const setHidden = useCallback(
    (hidden: boolean) => write(walkthrough.id, { ...read(walkthrough.id), hidden }),
    [walkthrough.id],
  );

  const reset = useCallback(
    () => write(walkthrough.id, { done: [], hidden: false }),
    [walkthrough.id],
  );

  const currentIndex = walkthrough.steps.findIndex((step) => !state.done.includes(step.id));
  return {
    done: state.done,
    hidden: state.hidden,
    complete: currentIndex === -1,
    currentStepId: currentIndex === -1 ? null : walkthrough.steps[currentIndex]!.id,
    markDone,
    setHidden,
    reset,
  };
}
