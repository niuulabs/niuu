import { useMemo } from 'react';
import { useSkuldChat } from './useSkuldChat';
import type { SkuldChatMessage } from './useSkuldChat';

export interface UseAgentDetailReturn {
  readonly messages: readonly SkuldChatMessage[];
  readonly connected: boolean;
  readonly isRunning: boolean;
}

const EMPTY: UseAgentDetailReturn = {
  messages: [],
  connected: false,
  isRunning: false,
};

/**
 * Convert an HTTP(S) gateway URL to a WebSocket URL by appending /ws and
 * swapping the scheme.  Returns null when gatewayUrl is null/empty.
 *
 * Examples:
 *   http://host:8080  → ws://host:8080/ws
 *   https://host:8080 → wss://host:8080/ws
 *   ws://host:8080    → ws://host:8080/ws  (scheme already correct)
 */
function toWsUrl(gatewayUrl: string): string | null {
  try {
    const url = new URL(gatewayUrl);
    if (url.protocol === 'http:') {
      url.protocol = 'ws:';
    } else if (url.protocol === 'https:') {
      url.protocol = 'wss:';
    }
    // Append /ws path — strip any trailing slash first
    url.pathname = url.pathname.replace(/\/$/, '') + '/ws';
    return url.toString();
  } catch {
    return null;
  }
}

/**
 * Thin wrapper around `useSkuldChat` scoped to a single Ravn agent gateway.
 *
 * Constructs the WebSocket URL from the HTTP gateway URL (http→ws, appending
 * /ws) and returns the full event stream — thinking blocks, tool calls, and
 * streaming messages — for display in the agent detail panel.
 *
 * Returns empty state when gatewayUrl is null (no agent selected).
 */
export function useAgentDetail(gatewayUrl: string | null): UseAgentDetailReturn {
  const wsUrl = useMemo(() => {
    if (!gatewayUrl) return null;
    return toWsUrl(gatewayUrl);
  }, [gatewayUrl]);

  const { messages, connected, isRunning } = useSkuldChat(wsUrl);

  if (!gatewayUrl) return EMPTY;

  return { messages, connected, isRunning };
}
