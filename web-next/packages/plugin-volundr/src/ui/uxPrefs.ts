import { useSyncExternalStore } from 'react';
// niuu:ux: "debug metadata" visibility toggle.
//
// Platform plumbing — forge/cluster IDs, pod GUIDs, owner ids and similar — is
// useful when debugging Volundr but noise for the operator. It is hidden by
// default and can be revealed by setting `niuu.compactUx.showDebugMeta` to "1" in
// localStorage (same `niuu.compactUx.*` namespace as the chat UX prefs).
const SHOW_DEBUG_META_KEY = 'niuu.compactUx.showDebugMeta';

/** True only when the operator has explicitly opted into debug metadata. */
export function getShowDebugMeta(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    const v = window.localStorage.getItem(SHOW_DEBUG_META_KEY);
    return v === '1' || v === 'true';
  } catch {
    return false;
  }
}

/** Persist the debug-metadata toggle (mainly for a future settings surface). */
export function setShowDebugMeta(show: boolean): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(SHOW_DEBUG_META_KEY, show ? '1' : '0');
    window.dispatchEvent(new Event('niuu:debug-metadata'));
  } catch {
    // localStorage may be unavailable
  }
}

function subscribe(listener: () => void) {
  window.addEventListener('storage', listener);
  window.addEventListener('niuu:debug-metadata', listener);
  return () => {
    window.removeEventListener('storage', listener);
    window.removeEventListener('niuu:debug-metadata', listener);
  };
}
export function useShowDebugMeta() {
  return useSyncExternalStore(subscribe, getShowDebugMeta, () => false);
}
