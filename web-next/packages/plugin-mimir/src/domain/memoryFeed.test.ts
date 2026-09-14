import { describe, it, expect } from 'vitest';
import { mergeMemoryFeed, pagesInFeed, MEMORY_EVENT_LABEL } from './memoryFeed';
import type { RecentWrite } from '../ports/IMountAdapter';
import type { ActivityEvent } from './lint';

function write(overrides: Partial<RecentWrite> = {}): RecentWrite {
  return {
    id: 'w1',
    timestamp: '2026-04-19T09:00:00Z',
    mount: 'shared',
    page: '/arch/overview',
    ravn: 'ravn-fjolnir',
    kind: 'write',
    message: 'added a key fact',
    ...overrides,
  };
}

function activity(overrides: Partial<ActivityEvent> = {}): ActivityEvent {
  return {
    id: 'a1',
    timestamp: '2026-04-19T08:00:00Z',
    kind: 'ingest',
    mount: 'local',
    ravn: 'ravn-skald',
    message: 'ingested karpathy.github.io',
    ...overrides,
  };
}

describe('mergeMemoryFeed', () => {
  it('names a plain write "fact added"', () => {
    const [event] = mergeMemoryFeed([write()], [], 10);
    expect(event?.kind).toBe('fact-added');
    expect(event?.label).toBe(MEMORY_EVENT_LABEL['fact-added']);
  });

  it('names a write that describes a rewrite "belief revised"', () => {
    const [event] = mergeMemoryFeed([write({ message: 'rewrote compiled truth' })], [], 10);
    expect(event?.kind).toBe('belief-revised');
  });

  it.each([
    ['compile', 'page-compiled'],
    ['lint-fix', 'tidied'],
    ['dream', 'dreamed'],
  ] as const)('maps the %s write kind', (kind, expected) => {
    const [event] = mergeMemoryFeed([write({ kind })], [], 10);
    expect(event?.kind).toBe(expected);
  });

  it.each([
    ['ingest', 'source-ingested'],
    ['lint', 'tidied'],
    ['dream', 'dreamed'],
    ['query', 'asked'],
  ] as const)('maps the %s activity kind', (kind, expected) => {
    const [event] = mergeMemoryFeed([], [activity({ kind })], 10);
    expect(event?.kind).toBe(expected);
  });

  it('carries who wrote it, the mount and the page', () => {
    const [event] = mergeMemoryFeed([write()], [], 10);
    expect(event).toMatchObject({
      who: 'ravn-fjolnir',
      mount: 'shared',
      page: '/arch/overview',
    });
  });

  it('leaves the page empty for an activity entry without one', () => {
    const [event] = mergeMemoryFeed([], [activity()], 10);
    expect(event?.page).toBe('');
  });

  it('sorts newest first across both feeds', () => {
    const events = mergeMemoryFeed(
      [write({ id: 'w-old', timestamp: '2026-04-18T09:00:00Z' })],
      [activity({ id: 'a-new', timestamp: '2026-04-20T09:00:00Z' })],
      10,
    );
    expect(events.map((event) => event.id)).toEqual(['activity-a-new', 'write-w-old']);
  });

  it('keeps the write when both feeds report the same happening', () => {
    const events = mergeMemoryFeed(
      [write()],
      [
        activity({
          timestamp: '2026-04-19T09:00:00Z',
          mount: 'shared',
          message: 'added a key fact',
          kind: 'write',
        }),
      ],
      10,
    );
    expect(events).toHaveLength(1);
    expect(events[0]?.id).toBe('write-w1');
  });

  it('honours the limit', () => {
    const writes = Array.from({ length: 5 }, (_, index) =>
      write({ id: `w${index}`, timestamp: `2026-04-0${index + 1}T09:00:00Z` }),
    );
    expect(mergeMemoryFeed(writes, [], 2)).toHaveLength(2);
  });
});

describe('pagesInFeed', () => {
  it('lists distinct pages, newest first, without the pageless entries', () => {
    const events = mergeMemoryFeed(
      [
        write({ id: 'w1', page: '/a', timestamp: '2026-04-19T09:00:00Z' }),
        write({ id: 'w2', page: '/b', timestamp: '2026-04-18T09:00:00Z' }),
        write({ id: 'w3', page: '/a', timestamp: '2026-04-17T09:00:00Z' }),
        write({ id: 'w4', page: '', timestamp: '2026-04-16T09:00:00Z' }),
      ],
      [],
      10,
    );
    expect(pagesInFeed(events, 8)).toEqual(['/a', '/b']);
  });

  it('stops at the limit', () => {
    const events = mergeMemoryFeed(
      [
        write({ id: 'w1', page: '/a', timestamp: '2026-04-19T09:00:00Z' }),
        write({ id: 'w2', page: '/b', timestamp: '2026-04-18T09:00:00Z' }),
      ],
      [],
      10,
    );
    expect(pagesInFeed(events, 1)).toEqual(['/a']);
  });
});
