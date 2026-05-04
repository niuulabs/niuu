import { useRef, useCallback, useEffect } from 'react';
import { getAccessToken } from '@niuulabs/query';

interface UseWebSocketOptions {
  onOpen?: () => void;
  onMessage?: (data: string) => void;
  onClose?: (code: number, reason: string) => void;
  onError?: (event: Event) => void;
  snapshotHandlersPerConnection?: boolean;
  reconnect?: boolean;
  maxReconnectAttempts?: number;
  reconnectBaseDelay?: number;
  reconnectMaxDelay?: number;
}

interface UseWebSocketReturn {
  sendJson: (data: unknown) => void;
}

export function useWebSocket(
  url: string | null,
  options: UseWebSocketOptions = {},
): UseWebSocketReturn {
  const {
    onOpen,
    onMessage,
    onClose,
    onError,
    snapshotHandlersPerConnection = false,
    reconnect = true,
    maxReconnectAttempts = 10,
    reconnectBaseDelay = 1_000,
    reconnectMaxDelay = 30_000,
  } = options;

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const intentionalCloseRef = useRef(false);

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

  const snapshotHandlersPerConnectionRef = useRef(snapshotHandlersPerConnection);

  useEffect(() => {
    snapshotHandlersPerConnectionRef.current = snapshotHandlersPerConnection;
  }, [snapshotHandlersPerConnection]);

  const reconnectRef = useRef(reconnect);
  const maxReconnectAttemptsRef = useRef(maxReconnectAttempts);
  const reconnectBaseDelayRef = useRef(reconnectBaseDelay);
  const reconnectMaxDelayRef = useRef(reconnectMaxDelay);

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

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!url) return;

    intentionalCloseRef.current = false;

    const doConnect = (wsUrl: string) => {
      clearReconnectTimer();

      let finalUrl = wsUrl;
      const token = getAccessToken();
      if (token) {
        const sep = wsUrl.includes('?') ? '&' : '?';
        finalUrl = `${wsUrl}${sep}access_token=${encodeURIComponent(token)}`;
      }

      const parsed = new URL(finalUrl);
      if (parsed.protocol !== 'ws:' && parsed.protocol !== 'wss:') {
        onErrorRef.current?.(new Event('error'));
        return;
      }

      const ws = new WebSocket(finalUrl);
      wsRef.current = ws;
      const handlers = snapshotHandlersPerConnectionRef.current
        ? {
            onOpen,
            onMessage,
            onClose,
            onError,
          }
        : null;

      ws.onopen = () => {
        reconnectAttemptsRef.current = 0;
        if (handlers) {
          handlers.onOpen?.();
          return;
        }
        onOpenRef.current?.();
      };

      ws.onmessage = (event: MessageEvent) => {
        if (wsRef.current !== ws) return;
        if (handlers) {
          handlers.onMessage?.(event.data as string);
          return;
        }
        onMessageRef.current?.(event.data as string);
      };

      ws.onclose = (event: CloseEvent) => {
        // Ignore closes from sockets that have already been replaced.
        // This matches the older @web hook and prevents a stale socket
        // from flipping the UI back to "disconnected" after a newer
        // connection is already active.
        if (wsRef.current !== ws) return;

        if (handlers) {
          handlers.onClose?.(event.code, event.reason);
        } else {
          onCloseRef.current?.(event.code, event.reason);
        }

        if (intentionalCloseRef.current || !reconnectRef.current) return;
        if (reconnectAttemptsRef.current >= maxReconnectAttemptsRef.current) return;

        const delay = Math.min(
          reconnectBaseDelayRef.current * 2 ** reconnectAttemptsRef.current,
          reconnectMaxDelayRef.current,
        );
        reconnectAttemptsRef.current += 1;
        reconnectTimerRef.current = setTimeout(() => doConnect(wsUrl), delay);
      };

      ws.onerror = (event: Event) => {
        if (wsRef.current !== ws) return;
        if (handlers) {
          handlers.onError?.(event);
          return;
        }
        onErrorRef.current?.(event);
      };
    };

    doConnect(url);

    return () => {
      intentionalCloseRef.current = true;
      clearReconnectTimer();
      wsRef.current?.close();
      wsRef.current = null;
      reconnectAttemptsRef.current = 0;
    };
  }, [url, clearReconnectTimer]);

  const sendJson = useCallback((data: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  return { sendJson };
}
