import { describe, expect, it } from 'vitest';
import { countWords, greeting, stateSentence } from './homeCopy';

describe('homeCopy', () => {
  it('spells small counts and leaves large ones as numerals', () => {
    expect(countWords(0)).toBe('No');
    expect(countWords(3)).toBe('Three');
    expect(countWords(42)).toBe('42');
  });

  it('greets by first name when identity gives one', () => {
    expect(greeting('Jozef van Eenbergen')).toBe('Good morning, Jozef.');
    expect(greeting('  ')).toBe('Good morning.');
    expect(greeting(undefined)).toBe('Good morning.');
    expect(greeting(null)).toBe('Good morning.');
  });

  it('says where things stand in one sentence', () => {
    expect(stateSentence({ realms: 0, running: 0, needsYou: 0 })).toBe(
      'Nothing is running yet. Pick one of the three below.',
    );
    expect(stateSentence({ realms: 1, running: 0, needsYou: 0 })).toBe('One realm.');
    expect(stateSentence({ realms: 3, running: 2, needsYou: 1 })).toBe(
      'Three realms, two sessions running, one thing needs you.',
    );
    expect(stateSentence({ realms: 0, running: 1, needsYou: 4 })).toBe(
      'One session running, four things need you.',
    );
  });
});
