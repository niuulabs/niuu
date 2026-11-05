import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useDictation } from './useDictation';

class FakeRecognition {
  static last: FakeRecognition | null = null;
  continuous = false;
  interimResults = true;
  lang = '';
  onresult: ((event: unknown) => void) | null = null;
  onerror: ((event: { error: string }) => void) | null = null;
  onend: (() => void) | null = null;
  start = vi.fn();
  stop = vi.fn(() => this.onend?.());
  abort = vi.fn();
  constructor() {
    FakeRecognition.last = this;
  }
  say(text: string, isFinal = true) {
    this.onresult?.({ resultIndex: 0, results: [{ isFinal, 0: { transcript: text } }] });
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
  FakeRecognition.last = null;
});

describe('useDictation', () => {
  it('offers nothing where the browser cannot listen', () => {
    const { result } = renderHook(() => useDictation(vi.fn()));
    expect(result.current.supported).toBe(false);
    act(() => result.current.toggle());
    expect(result.current.listening).toBe(false);
  });

  it('hands each finished phrase over and stops on a second toggle', () => {
    vi.stubGlobal('webkitSpeechRecognition', FakeRecognition);
    const onPhrase = vi.fn();
    const { result } = renderHook(() => useDictation(onPhrase));
    expect(result.current.supported).toBe(true);

    act(() => result.current.toggle());
    const recognition = FakeRecognition.last!;
    expect(recognition.start).toHaveBeenCalled();
    expect(recognition.continuous).toBe(true);
    expect(result.current.listening).toBe(true);

    act(() => recognition.say('half a thought', false));
    act(() => recognition.say(' ship the fix '));
    expect(onPhrase).toHaveBeenCalledTimes(1);
    expect(onPhrase).toHaveBeenCalledWith('ship the fix');

    act(() => result.current.toggle());
    expect(recognition.stop).toHaveBeenCalled();
    expect(result.current.listening).toBe(false);
  });

  it('says why when the microphone is refused, and clears it on the next start', () => {
    vi.stubGlobal('SpeechRecognition', FakeRecognition);
    const { result } = renderHook(() => useDictation(vi.fn()));
    act(() => result.current.toggle());
    act(() => {
      FakeRecognition.last!.onerror?.({ error: 'not-allowed' });
      FakeRecognition.last!.onend?.();
    });
    expect(result.current.error).toMatch(/Microphone access was refused/);
    expect(result.current.listening).toBe(false);

    act(() => result.current.toggle());
    act(() => FakeRecognition.last!.onerror?.({ error: 'network' }));
    expect(result.current.error).toBe('Dictation stopped: network.');
  });

  it('stops listening when the composer goes away', () => {
    vi.stubGlobal('webkitSpeechRecognition', FakeRecognition);
    const { result, unmount } = renderHook(() => useDictation(vi.fn()));
    act(() => result.current.toggle());
    unmount();
    expect(FakeRecognition.last!.abort).toHaveBeenCalled();
  });
});
