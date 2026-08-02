import { describe, it, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { createElement } from 'react';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { useEvents } from './useEvents';
import type { ObservatoryEvent } from '../domain';
import type { IEventStream } from '../ports';

function wrap(stream: IEventStream) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return createElement(
      ServicesProvider,
      { services: { 'observatory.events': stream } },
      children,
    );
  };
}

function createEvent(id: string): ObservatoryEvent {
  return {
    id,
    time: '12:00:00',
    type: 'RUN',
    subject: `subject-${id}`,
    body: `body-${id}`,
  };
}

function createStreamHarness() {
  let listener: ((event: ObservatoryEvent) => void) | null = null;
  const unsubscribe = vi.fn();
  const stream: IEventStream = {
    subscribe(next) {
      listener = next;
      return unsubscribe;
    },
  };

  return {
    stream,
    unsubscribe,
    emit(event: ObservatoryEvent) {
      if (!listener) throw new Error('listener not registered');
      listener(event);
    },
  };
}

describe('useEvents', () => {
  it('ignores duplicate events with the same id', () => {
    const harness = createStreamHarness();
    const { result } = renderHook(() => useEvents(), { wrapper: wrap(harness.stream) });

    act(() => {
      harness.emit(createEvent('evt-1'));
      harness.emit(createEvent('evt-1'));
    });

    expect(result.current.map((event) => event.id)).toEqual(['evt-1']);
  });

  it('drops an event when the condition it reported clears', () => {
    // Discovery warnings are conditions, not incidents. Without a retraction
    // a warning stayed on the log until a hundred newer events pushed it off,
    // so a fault fixed an hour ago read exactly like one still happening.
    const harness = createStreamHarness();
    const { result } = renderHook(() => useEvents(), { wrapper: wrap(harness.stream) });

    act(() => {
      harness.emit(createEvent('discovery:ravn'));
      harness.emit(createEvent('evt-other'));
    });
    expect(result.current.map((e) => e.id)).toEqual(['discovery:ravn', 'evt-other']);

    act(() => {
      harness.emit({ ...createEvent('discovery:ravn'), resolved: true });
    });

    expect(result.current.map((e) => e.id)).toEqual(['evt-other']);
  });

  it('ignores a retraction for something it never showed', () => {
    const harness = createStreamHarness();
    const { result } = renderHook(() => useEvents(), { wrapper: wrap(harness.stream) });

    act(() => {
      harness.emit(createEvent('evt-1'));
      harness.emit({ ...createEvent('never-seen'), resolved: true });
    });

    expect(result.current.map((e) => e.id)).toEqual(['evt-1']);
  });

  it('keeps only the most recent 100 events', () => {
    const harness = createStreamHarness();
    const { result } = renderHook(() => useEvents(), { wrapper: wrap(harness.stream) });

    act(() => {
      for (let index = 0; index < 101; index += 1) {
        harness.emit(createEvent(`evt-${index}`));
      }
    });

    expect(result.current).toHaveLength(100);
    expect(result.current[0]?.id).toBe('evt-1');
    expect(result.current.at(-1)?.id).toBe('evt-100');
  });
});
