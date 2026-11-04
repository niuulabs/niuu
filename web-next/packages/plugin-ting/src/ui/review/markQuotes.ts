const QUOTE_ATTRIBUTE = 'quote';

function wrap(node: Text, start: number, quote: string, onClick?: (quote: string) => void): void {
  const range = document.createRange();
  range.setStart(node, start);
  range.setEnd(node, start + quote.length);
  const mark = document.createElement('mark');
  mark.dataset[QUOTE_ATTRIBUTE] = quote;
  mark.title = 'Open in the conversation';
  if (onClick) mark.addEventListener('click', () => onClick(quote));
  range.surroundContents(mark);
}

function markOne(root: HTMLElement, quote: string, onClick?: (quote: string) => void): void {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node as Text;
    if (text.parentElement?.closest('mark')) continue;
    const start = text.data.indexOf(quote);
    if (start < 0) continue;
    wrap(text, start, quote, onClick);
    return;
  }
}

/**
 * Mark each quoted passage where it still appears, and return how to undo that.
 *
 * Quotes are matched against the text as rendered, so a passage that a later
 * revision rewrote simply stops being marked.
 */
export function markQuotes(
  root: HTMLElement,
  quotes: readonly string[],
  onClick?: (quote: string) => void,
): () => void {
  for (const quote of quotes) markOne(root, quote, onClick);
  return () => {
    for (const mark of root.querySelectorAll('mark')) {
      mark.replaceWith(...mark.childNodes);
    }
    root.normalize();
  };
}
