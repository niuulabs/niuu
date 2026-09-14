import { useState, type FormEvent } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { Textarea } from '@niuulabs/ui';
import { Sparkles } from 'lucide-react';

const PLACEHOLDER =
  'Keep niuulabs/lexi-api shippable, work the LXA board in priority order, and ask me before anything deploys.';

/** One sentence is enough: it becomes a draft realm the resident fills in. */
export function SentenceComposer({ rows = 1 }: { rows?: number } = {}) {
  const navigate = useNavigate();
  const [text, setText] = useState('');
  const ready = text.trim().length > 0;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready) return;
    void navigate({ to: '/realms/new', search: { sentence: text.trim() } as never });
  }

  return (
    <form
      onSubmit={submit}
      className="niuu:flex niuu:items-center niuu:gap-3 niuu:rounded-xl niuu:border niuu:border-brand/40 niuu:bg-brand/5 niuu:py-3 niuu:pl-4 niuu:pr-3.5"
      data-testid="sentence-composer"
    >
      <Sparkles size={18} className="niuu:shrink-0 niuu:text-brand" aria-hidden="true" />
      <div className="niuu:flex niuu:flex-1 niuu:flex-col niuu:gap-0.5">
        <Textarea
          aria-label="Describe the realm in one sentence"
          placeholder={PLACEHOLDER}
          rows={rows}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) submit(event);
          }}
          data-testid="sentence-input"
        />
        <span className="niuu:text-xs niuu:text-text-muted">
          One sentence is enough. The resident reads it, fills in what it can, and asks for the rest
          before anything starts.
        </span>
      </div>
      <button
        type="submit"
        disabled={!ready}
        className="niuu:rounded-full niuu:bg-brand niuu:px-5 niuu:py-2 niuu:text-sm niuu:font-semibold niuu:text-bg-primary niuu:disabled:opacity-40"
        data-testid="sentence-submit"
      >
        Start a realm
      </button>
    </form>
  );
}
