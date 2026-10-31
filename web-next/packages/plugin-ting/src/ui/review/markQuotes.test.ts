import { beforeEach, describe, expect, it, vi } from 'vitest';
import { markQuotes } from './markQuotes';

describe('markQuotes', () => {
  let root: HTMLElement;

  beforeEach(() => {
    root = document.createElement('div');
    root.innerHTML = '<p>Add one span. Trace every tick so nothing is missed.</p><p>Done.</p>';
  });

  it('marks a quoted passage where it appears', () => {
    markQuotes(root, ['Trace every tick']);

    expect(root.querySelector('mark')?.textContent).toBe('Trace every tick');
  });

  it('leaves a passage a later revision rewrote unmarked', () => {
    markQuotes(root, ['Sample one tick in ten']);

    expect(root.querySelector('mark')).toBeNull();
  });

  it('opens the conversation from a mark', () => {
    const onClick = vi.fn();
    markQuotes(root, ['Trace every tick'], onClick);

    root.querySelector('mark')?.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    expect(onClick).toHaveBeenCalledWith('Trace every tick');
  });

  it('marks each quote once and never inside another mark', () => {
    markQuotes(root, ['Trace every tick', 'every']);

    expect([...root.querySelectorAll('mark')].map((mark) => mark.textContent)).toEqual([
      'Trace every tick',
    ]);
  });

  it('puts the text back when undone', () => {
    const before = root.innerHTML;
    const undo = markQuotes(root, ['Trace every tick']);

    undo();

    expect(root.querySelector('mark')).toBeNull();
    expect(root.innerHTML).toBe(before);
  });
});
