import { useRef, useCallback, useEffect } from 'react';
import { getAccessToken } from '@/modules/volundr/adapters/api/client';

export type WebSocketStatus = 'connecting' | 'connected' | 'disconnected' | 'error';

interface UseWebSocketOptions {
  /** Called when the connection opens */
  onOpen?: () => void;
  /** Called when a message is received */
  onMessage?: (data: string) => void;
  /** Called when the connection closes */
  onClose?: (code: number, reason: string) => void;
  /** Called on error */
  onError?: (event: Event) => void;
  /** Whether to automatically reconnect on close (default: true) */
  reconnect?: boolean;
  /** Max reconnect attempts before giving up (default: 10) */
  maxReconnectAttempts?: number;
  /** Base delay in ms for exponential backoff (default: 1000) */
  reconnectBaseDelay?: number;
  /** Max delay in ms for exponential backoff (default: 30000) */
  reconnectMaxDelay?: number;
  /** Whether to queue outbound messages while disconnected (default: true) */
  queueWhenDisconnected?: boolean;
}

interface UseWebSocketReturn {
  /** Send a string message */
  send: (data: string) => void;
  /** Send a typed JSON message */
  sendJson: (data: unknown) => void;
  /** Close the connection */
  close: () => void;
  /** Get the current WebSocket instance (for checking readyState etc.) */
  getSocket: () => WebSocket | null;
}

/**
 * Lightweight WebSocket hook with reconnection.
 * Connect by calling the hook with a URL; disconnect by unmounting
 * or passing null as the URL.
 */
export function useWebSocket(
  url: string | null,
  options: UseWebSocketOptions = {}
): UseWebSocketReturn {
  const {
    onOpen,
    onMessage,
    onClose,
    onError,
    reconnect = true,
    maxReconnectAttempts = 10,
    reconnectBaseDelay = 1000,
    reconnectMaxDelay = 30000,
    queueWhenDisconnected = true,
  } = options;

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const intentionalCloseRef = useRef(false);
  const pendingMessagesRef = useRef<string[]>([]);

  // Keep latest callbacks in refs so we don't re-connect on callback changes
  const onOpenRef = useRef(onOpen);
  const onMessageRef = useRef(onMessage);
  const onCloseRef = useRef(onClose);
  const onErrorRef = useRef(onError);

  useEffect(() => {
    onOpenRef.current = onOpen;
  }, [onOpen]);

  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  // Store reconnect config in refs so the connect closure reads latest values
  const reconnectRef = useRef(reconnect);
  const maxReconnectAttemptsRef = useRef(maxReconnectAttempts);
  const reconnectBaseDelayRef = useRef(reconnectBaseDelay);
  const reconnectMaxDelayRef = useRef(reconnectMaxDelay);
  const queueWhenDisconnectedRef = useRef(queueWhenDisconnected);

  useEffect(() => {
    reconnectRef.current = reconnect;
  }, [reconnect]);

  useEffect(() => {
    maxReconnectAttemptsRef.current = maxReconnectAttempts;
  }, [maxReconnectAttempts]);

  useEffect(() => {
    reconnectBaseDelayRef.current = reconnectBaseDelay;
  }, [reconnectBaseDelay]);

  useEffect(() => {
    reconnectMaxDelayRef.current = reconnectMaxDelay;
  }, [reconnectMaxDelay]);

  useEffect(() => {
    queueWhenDisconnectedRef.current = queueWhenDisconnected;
  }, [queueWhenDisconnected]);

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const flushPendingMessages = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || pendingMessagesRef.current.length === 0) {
      return;
    }

    const pending = pendingMessagesRef.current.splice(0, pendingMessagesRef.current.length);
    for (const message of pending) {
      ws.send(message);
    }
  }, []);

  // Connect / disconnect when url changes.
  // All connection logic lives inside this single effect to avoid
  // circular-reference issues with the linter.
  useEffect(() => {
    if (!url) {
      return;
    }

    intentionalCloseRef.current = false;

    const doConnect = (wsUrl: string) => {
      clearReconnectTimer();

      // Append access token as query parameter for gateway JWT validation
      let finalUrl = wsUrl;
      const token = getAccessToken();
      if (token) {
        const sep = wsUrl.includes('?') ? '&' : '?';
        finalUrl = `${wsUrl}${sep}access_token=${encodeURIComponent(token)}`;
      }

      // Validate URL before connecting — reject non-ws(s) schemes
      const parsed = new URL(finalUrl);
      if (parsed.protocol !== 'ws:' && parsed.protocol !== 'wss:') {
        onErrorRef.current?.(new Event('error'));
        return;
      }

      const ws = new WebSocket(finalUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectAttemptsRef.current = 0;
        flushPendingMessages();
        onOpenRef.current?.();
      };

      ws.onmessage = (event: MessageEvent) => {
        onMessageRef.current?.(event.data as string);
      };

      ws.onclose = (event: CloseEvent) => {
        onCloseRef.current?.(event.code, event.reason);

        // If this WS was replaced by a newer connection, ignore it.
        // Prevents stale reconnect when URL changes and the old WS
        // fires onclose after intentionalCloseRef was already reset.
        if (wsRef.current !== ws) {
          return;
        }

        if (intentionalCloseRef.current) {
          return;
        }

        if (!reconnectRef.current) {
          return;
        }

        if (reconnectAttemptsRef.current >= maxReconnectAttemptsRef.current) {
          return;
        }

        const delay = Math.min(
          reconnectBaseDelayRef.current * Math.pow(2, reconnectAttemptsRef.current),
          reconnectMaxDelayRef.current
        );
        reconnectAttemptsRef.current += 1;
        reconnectTimerRef.current = setTimeout(() => doConnect(wsUrl), delay);
      };

      ws.onerror = (event: Event) => {
        onErrorRef.current?.(event);
      };
    };

    doConnect(url);

    return () => {
      intentionalCloseRef.current = true;
      clearReconnectTimer();
      pendingMessagesRef.current = [];
      wsRef.current?.close();
      wsRef.current = null;
      reconnectAttemptsRef.current = 0;
    };
  }, [url, clearReconnectTimer, flushPendingMessages]);

  const enqueueOrSend = useCallback(
    (message: string) => {
      const ws = wsRef.current;
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(message);
        return;
      }
      if (!url || intentionalCloseRef.current) {
        return;
      }
      if (!queueWhenDisconnectedRef.current) {
        return;
      }
      pendingMessagesRef.current.push(message);
    },
    [url]
  );

  const send = useCallback(
    (data: string) => {
      enqueueOrSend(data);
    },
    [enqueueOrSend]
  );

  const sendJson = useCallback(
    (data: unknown) => {
      enqueueOrSend(JSON.stringify(data));
    },
    [enqueueOrSend]
  );

  const close = useCallback(() => {
    intentionalCloseRef.current = true;
    clearReconnectTimer();
    pendingMessagesRef.current = [];
    wsRef.current?.close();
  }, [clearReconnectTimer]);

  const getSocket = useCallback(() => wsRef.current, []);

  return { send, sendJson, close, getSocket };
}
