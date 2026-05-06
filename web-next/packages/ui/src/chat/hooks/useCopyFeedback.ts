import { useCallback, useEffect, useRef, useState } from 'react';

const FEEDBACK_MS = 2000;

/**
 * Manages copy-to-clipboard state with a timed reset and proper cleanup on unmount.
 * Returns [copied, copy] — call copy() to trigger the clipboard write + feedback timer.
 */
export function useCopyFeedback(text: string): [boolean, () => void] {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
    };
  }, []);

  const copy = useCallback(() => {
    navigator.clipboard?.writeText(text).catch(() => undefined);
    setCopied(true);
    if (timerRef.current !== null) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setCopied(false), FEEDBACK_MS);
  }, [text]);

  return [copied, copy];
}
