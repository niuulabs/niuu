import { useState } from 'react';

export type AmbientVariant = 'topology' | 'constellation' | 'lattice';

const STORAGE_KEY = 'niuu-login-ambient';
const VALID: AmbientVariant[] = ['topology', 'constellation', 'lattice'];
const DEFAULT: AmbientVariant = 'topology';

function readStored(): AmbientVariant {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw && (VALID as string[]).includes(raw)) return raw as AmbientVariant;
  } catch {
    // localStorage unavailable (SSR, private browsing)
  }
  return DEFAULT;
}

/**
 * Manages which ambient background variant is shown on the login page.
 *
 * - Persists the selection to localStorage under `niuu-login-ambient`.
 * - Falls back to `'topology'` when the stored value is missing or invalid.
 */
export function useAmbient(): [AmbientVariant, (v: AmbientVariant) => void] {
  const [ambient, setAmbientState] = useState<AmbientVariant>(readStored);

  const setAmbient = (v: AmbientVariant) => {
    try {
      localStorage.setItem(STORAGE_KEY, v);
    } catch {
      // Ignore write failures (private browsing, quota exceeded)
    }
    setAmbientState(v);
  };

  return [ambient, setAmbient];
}
