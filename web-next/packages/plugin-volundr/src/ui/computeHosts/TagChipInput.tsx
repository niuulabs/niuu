import { useId, useState, type KeyboardEvent, type ReactNode } from 'react';
import { parseTagInput } from '../../domain/computeHosts';

export interface TagChipInputProps {
  /** Names the control for assistive technology ("Tags", "Filter by tags"). */
  label: string;
  value: readonly string[];
  onChange: (tags: string[]) => void;
  /** Tags worth offering while typing. */
  suggestions?: readonly string[];
  placeholder?: string;
  disabled?: boolean;
  /** Controls that belong in the same box, such as the all/any switch. */
  children?: ReactNode;
}

/**
 * Tags as chips with one input: Enter, comma or space adds what was typed, the chip's
 * cross or Backspace on an empty input removes one. The same selector Launch uses to
 * target a Forge by tags, here for filtering hosts and for choosing a new host's tags.
 */
export function TagChipInput({
  label,
  value,
  onChange,
  suggestions = [],
  placeholder = 'add tag…',
  disabled = false,
  children,
}: TagChipInputProps) {
  const [draft, setDraft] = useState('');
  const listId = useId();

  const commit = (text: string) => {
    const added = parseTagInput(text).filter((tag) => !value.includes(tag));
    setDraft('');
    if (added.length) onChange([...value, ...added]);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault();
      commit(draft);
      return;
    }
    if (event.key === 'Backspace' && draft === '' && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  };

  return (
    <div
      role="group"
      aria-label={label}
      className="niuu:flex niuu:min-w-[16rem] niuu:flex-wrap niuu:items-center niuu:gap-1.5 niuu:rounded-lg niuu:border niuu:border-border niuu:bg-bg-secondary niuu:px-2 niuu:py-1"
    >
      {value.map((tag) => (
        <span
          key={tag}
          className="niuu:inline-flex niuu:items-center niuu:gap-1 niuu:rounded-md niuu:border niuu:border-brand/35 niuu:bg-brand/15 niuu:px-2 niuu:py-0.5 niuu:text-xs niuu:text-brand"
        >
          {tag}
          <button
            type="button"
            aria-label={`Remove tag ${tag}`}
            disabled={disabled}
            className="niuu:text-brand niuu:hover:text-text-primary"
            onClick={() => onChange(value.filter((item) => item !== tag))}
          >
            ×
          </button>
        </span>
      ))}
      <input
        value={draft}
        disabled={disabled}
        placeholder={placeholder}
        aria-label={`Add to ${label}`}
        list={suggestions.length ? listId : undefined}
        className="niuu:w-24 niuu:bg-transparent niuu:text-sm niuu:text-text-primary niuu:outline-none niuu:placeholder:text-text-muted"
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={onKeyDown}
        onBlur={() => commit(draft)}
      />
      {suggestions.length > 0 ? (
        <datalist id={listId}>
          {suggestions
            .filter((tag) => !value.includes(tag))
            .map((tag) => (
              <option key={tag} value={tag} />
            ))}
        </datalist>
      ) : null}
      {children}
    </div>
  );
}
