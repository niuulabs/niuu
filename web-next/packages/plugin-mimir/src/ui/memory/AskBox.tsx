/**
 * AskBox — the one input on the memory screens.
 *
 * Enter (or the button) hands the question to the ask page; the box keeps its
 * own draft so typing never round-trips through the URL.
 */

import { useState } from 'react';
import { Search } from 'lucide-react';

export interface AskBoxProps {
  /** Question the box starts with — a later change from the URL replaces the draft. */
  value?: string;
  placeholder?: string;
  autoFocus?: boolean;
  onAsk: (question: string) => void;
}

export function AskBox({ value = '', placeholder, onAsk, autoFocus = false }: AskBoxProps) {
  const [draft, setDraft] = useState(value);
  // A question arriving from the URL replaces the draft; typing is otherwise
  // local, so asking never round-trips through the router.
  const [askedValue, setAskedValue] = useState(value);
  if (value !== askedValue) {
    setAskedValue(value);
    setDraft(value);
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const question = draft.trim();
    if (!question) return;
    onAsk(question);
  }

  return (
    <form
      className="niuu:flex niuu:items-center niuu:gap-3 niuu:rounded-xl niuu:border niuu:border-brand/40 niuu:bg-bg-secondary niuu:px-4 niuu:py-3"
      onSubmit={submit}
      data-testid="memory-ask-box"
    >
      <Search size={16} className="niuu:text-brand-300" aria-hidden="true" />
      <input
        type="search"
        className="niuu:flex-1 niuu:border-none niuu:bg-transparent niuu:text-sm niuu:text-text-primary niuu:outline-none"
        placeholder={placeholder ?? 'Ask memory a question…'}
        aria-label="Ask memory"
        value={draft}
        autoFocus={autoFocus}
        onChange={(event) => setDraft(event.target.value)}
      />
      <button
        type="submit"
        className="niuu:rounded-full niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-4 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-brand-300 niuu:disabled:opacity-40"
        disabled={draft.trim().length === 0}
      >
        Ask memory
      </button>
    </form>
  );
}
