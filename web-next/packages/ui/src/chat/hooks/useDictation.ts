import { useCallback, useEffect, useRef, useState } from 'react';

/** The part of the browser's speech recognition the composer uses. */
interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: SpeechResultEvent) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

interface SpeechResultEvent {
  resultIndex: number;
  results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }>;
}

type RecognitionClass = new () => SpeechRecognitionLike;

function recognitionClass(): RecognitionClass | null {
  if (typeof window === 'undefined') return null;
  const scope = window as unknown as {
    SpeechRecognition?: RecognitionClass;
    webkitSpeechRecognition?: RecognitionClass;
  };
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition ?? null;
}

const REFUSED = new Set(['not-allowed', 'service-not-allowed']);

export interface Dictation {
  /** The browser can listen; without it the composer offers no microphone. */
  supported: boolean;
  listening: boolean;
  /** Why listening stopped, in words; cleared when listening starts again. */
  error: string | null;
  toggle: () => void;
}

/**
 * Dictation into the composer, as Lexi's microphone: each finished phrase is handed to
 * `onPhrase`. A refusal or a recognition error stops listening and says why.
 */
export function useDictation(onPhrase: (text: string) => void): Dictation {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recognition = useRef<SpeechRecognitionLike | null>(null);
  const phrase = useRef(onPhrase);
  useEffect(() => {
    phrase.current = onPhrase;
  });

  const toggle = useCallback(() => {
    if (recognition.current) {
      recognition.current.stop();
      return;
    }
    const Recognition = recognitionClass();
    if (!Recognition) return;
    const next = new Recognition();
    next.continuous = true;
    next.interimResults = false;
    next.lang = navigator.language;
    next.onresult = (event) => {
      for (let index = event.resultIndex; index < event.results.length; index++) {
        const result = event.results[index];
        const text = result?.isFinal ? result[0].transcript.trim() : '';
        if (text) phrase.current(text);
      }
    };
    next.onerror = (event) => {
      setError(
        REFUSED.has(event.error)
          ? 'Microphone access was refused; allow it for this site to dictate.'
          : `Dictation stopped: ${event.error}.`,
      );
    };
    next.onend = () => {
      recognition.current = null;
      setListening(false);
    };
    recognition.current = next;
    setError(null);
    next.start();
    setListening(true);
  }, []);

  useEffect(() => () => recognition.current?.abort(), []);

  return { supported: recognitionClass() !== null, listening, error, toggle };
}
