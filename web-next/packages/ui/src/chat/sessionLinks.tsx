import { createContext, useContext, type MouseEvent, type ReactNode } from 'react';

/**
 * How a session reads at a glance when a transcript names it: working, waiting on the
 * operator, failed, or quiet. The page that knows the session list picks the tone.
 */
export type SessionLinkTone = 'ok' | 'warn' | 'critical' | 'muted';

/** One session a transcript card or relay header can point at. */
export interface SessionLink {
  id: string;
  name: string;
  /** "Working", "Needs you", "Idle": the session's state in the operator's words. */
  stateLabel: string;
  tone: SessionLinkTone;
  href: string;
}

export interface SessionLinksValue {
  /** The session a transcript names by id or by name; null when the list does not know it. */
  resolve: (idOrName: string) => SessionLink | null;
  /** Show that session in the host app. */
  open: (id: string) => void;
}

const SessionLinksContext = createContext<SessionLinksValue | null>(null);

export interface SessionLinksProviderProps {
  value: SessionLinksValue;
  children: ReactNode;
}

/**
 * The page that lists sessions provides this around the conversation, so the cards and
 * relay headers in the transcript can open the sessions they name. The chat package does
 * not know routes or the session list; without a provider the cards render without links.
 */
export function SessionLinksProvider({ value, children }: SessionLinksProviderProps) {
  return <SessionLinksContext.Provider value={value}>{children}</SessionLinksContext.Provider>;
}

/** The provided links, or null when no page provided any. */
export function useSessionLinks(): SessionLinksValue | null {
  return useContext(SessionLinksContext);
}

/**
 * A plain click opens the session in place; a modified click (new tab, new window) and a
 * middle click are left to the browser, which follows the link's `href`.
 */
export function sessionOpenHandler(links: SessionLinksValue, link: SessionLink) {
  return (event: MouseEvent<HTMLElement>) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0)
      return;
    event.preventDefault();
    links.open(link.id);
  };
}
