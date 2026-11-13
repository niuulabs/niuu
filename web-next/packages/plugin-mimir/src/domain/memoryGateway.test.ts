import { describe, expect, it } from 'vitest';
import { sourceLabel, sourcePlace, type ReviewSource } from './memoryGateway';

const base: ReviewSource = { ref: 'x', kind: 'tool_call', title: null, url: null, kept: true };

describe('sourceLabel', () => {
  it('names a source by its title, a ticket key, or its host', () => {
    expect(sourceLabel({ ...base, title: 'The ticket' })).toBe('The ticket');
    expect(sourceLabel({ ...base, url: 'https://linear.app/acme/issue/LIN-501/x' })).toBe(
      'LIN-501',
    );
    expect(sourceLabel({ ...base, url: 'https://example.com/a' })).toBe('example.com');
    expect(sourceLabel({ ...base, url: 'not a url' })).toBe('not a url');
  });

  it('says what an evidence kind is in plain words', () => {
    expect(sourceLabel({ ...base, kept: false, kind: 'source' })).toBe('a source no longer kept');
    const words = ['session_event', 'commit', 'room_message', 'statement', 'page', 'fact']
      .concat(['proposal', 'import'])
      .map((kind) => sourceLabel({ ...base, kind }));
    expect(words).toEqual([
      'its work in a session',
      'a commit',
      'a message in a room',
      "a person's statement",
      'a page',
      'a fact',
      'an earlier proposal',
      'a source',
    ]);
  });
});

describe('sourcePlace', () => {
  it('says where a cited record is', () => {
    const cited = { layer: 'org', layerName: '', ref: 'r', title: 't', url: null };
    expect(sourcePlace({ ...cited, kind: 'page' })).toBe('a page in memory');
    expect(sourcePlace({ ...cited, kind: 'source', layerName: 'Booking' })).toBe(
      'a source in Booking',
    );
  });
});
