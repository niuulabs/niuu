import { useEffect, useCallback, useRef } from 'react';

interface BroadcastMessage<T = unknown> {
  type: string;
  payload: T;
  timestamp: number;
  sourceId: string;
}

function generateSourceId(): string {
  return (
    globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
  );
}

/**
 * Hook for cross-window communication using BroadcastChannel API.
 * Messages are automatically ignored from the same source.
 */
export function useBroadcastChannel<T = unknown>(channelName: string) {
  const channelRef = useRef<BroadcastChannel | null>(null);
  const sourceId = useRef(generateSourceId());
  const listenersRef = useRef<Set<(msg: BroadcastMessage<T>) => void>>(new Set());

  useEffect(() => {
    channelRef.current = new BroadcastChannel(channelName);

    const handleMessage = (event: MessageEvent<BroadcastMessage<T>>) => {
      // Ignore messages from self
      if (event.data.sourceId === sourceId.current) {
        return;
      }
      listenersRef.current.forEach(listener => listener(event.data));
    };

    channelRef.current.addEventListener('message', handleMessage);

    return () => {
      channelRef.current?.removeEventListener('message', handleMessage);
      channelRef.current?.close();
    };
  }, [channelName]);

  const broadcast = useCallback((type: string, payload: T) => {
    channelRef.current?.postMessage({
      type,
      payload,
      timestamp: Date.now(),
      sourceId: sourceId.current,
    } satisfies BroadcastMessage<T>);
  }, []);

  const subscribe = useCallback((handler: (msg: BroadcastMessage<T>) => void) => {
    listenersRef.current.add(handler);
    return () => {
      listenersRef.current.delete(handler);
    };
  }, []);

  return { broadcast, subscribe };
}
