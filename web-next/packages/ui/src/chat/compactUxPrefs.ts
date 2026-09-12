import { useSyncExternalStore } from 'react';

// niuu:ux: compact-chat preferences.
//
// Defaults make this branch's chat view compact (action icons + agent avatar
// hidden, timestamp on hover). Each can be flipped back on at runtime via
// localStorage keys `niuu.compactUx.<key>` (no rebuild needed). A real settings
// surface can be layered on later — these read from the same keys.
export interface CompactUxChatPrefs {
  /** thumbs-up/down, regenerate, bookmark, copy row under each message */
  showMessageActions: boolean;
  /** the Hamr/Völundr avatar shown beside assistant messages */
  showAgentAvatar: boolean;
  /** message timestamp: hover-revealed (right), always shown, or never */
  timestamp: 'hover' | 'always' | 'never';
  /** copy affordance: a hover control near the message, or the inline row */
  copyMode: 'hover' | 'inline';
}

/** Codex-style conversation fold: question → "Worked" disclosure → answer. */
export type ConversationView = 'compact' | 'expanded';

const DEFAULTS: CompactUxChatPrefs = {
  showMessageActions: false,
  showAgentAvatar: false,
  timestamp: 'hover',
  copyMode: 'hover',
};

const CONVERSATION_VIEW_KEY = 'conversationView';
const CONVERSATION_VIEW_DEFAULT: ConversationView = 'compact';
const CONVERSATION_VIEW_VALUES = ['compact', 'expanded'] as const;

function readBool(key: string, fallback: boolean): boolean {
  if (typeof window === 'undefined') return fallback;
  try {
    const v = window.localStorage.getItem(`niuu.compactUx.${key}`);
    return v === null ? fallback : v === '1' || v === 'true';
  } catch {
    return fallback;
  }
}

function readEnum<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  if (typeof window === 'undefined') return fallback;
  try {
    const v = window.localStorage.getItem(`niuu.compactUx.${key}`) as T | null;
    return v && allowed.includes(v) ? v : fallback;
  } catch {
    return fallback;
  }
}

export function getCompactUxChatPrefs(): CompactUxChatPrefs {
  return {
    showMessageActions: readBool('showMessageActions', DEFAULTS.showMessageActions),
    showAgentAvatar: readBool('showAgentAvatar', DEFAULTS.showAgentAvatar),
    timestamp: readEnum('timestamp', ['hover', 'always', 'never'] as const, DEFAULTS.timestamp),
    copyMode: readEnum('copyMode', ['hover', 'inline'] as const, DEFAULTS.copyMode),
  };
}

/** Read the persisted conversation-fold view (defaults to "compact"). */
export function getConversationView(): ConversationView {
  return readEnum(CONVERSATION_VIEW_KEY, CONVERSATION_VIEW_VALUES, CONVERSATION_VIEW_DEFAULT);
}

/** Persist the conversation-fold view to localStorage. */
export function setConversationView(view: ConversationView): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(`niuu.compactUx.${CONVERSATION_VIEW_KEY}`, view);
  } catch {
    // localStorage may not be available
  }
}

const PREFERENCES_EVENT = 'niuu:chat-preferences';
function subscribe(listener: () => void) {
  window.addEventListener('storage', listener);
  window.addEventListener(PREFERENCES_EVENT, listener);
  return () => {
    window.removeEventListener('storage', listener);
    window.removeEventListener(PREFERENCES_EVENT, listener);
  };
}
const snapshot = () => JSON.stringify(getCompactUxChatPrefs());
export function useCompactUxChatPrefs(): CompactUxChatPrefs {
  return JSON.parse(useSyncExternalStore(subscribe, snapshot, () => JSON.stringify(DEFAULTS)));
}
export function setCompactUxChatPref<K extends keyof CompactUxChatPrefs>(
  key: K,
  value: CompactUxChatPrefs[K],
) {
  try {
    window.localStorage.setItem(`niuu.compactUx.${key}`, String(value));
    window.dispatchEvent(new Event(PREFERENCES_EVENT));
  } catch {
    /* Preferences are optional when storage is unavailable. */
  }
}
