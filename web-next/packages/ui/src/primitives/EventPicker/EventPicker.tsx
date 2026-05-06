import { useState, useCallback, useId } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { cn } from '../../utils/cn';
import type { EventSpec } from '@niuulabs/domain';
import './EventPicker.css';

export interface EventPickerProps {
  /** Currently selected event name. */
  value: string;
  onChange: (value: string) => void;
  /** Event specs available for selection. */
  catalog: EventSpec[];
  /** Allow creating a new event name inline. */
  allowNew?: boolean;
  /** Allow clearing the selection. */
  allowEmpty?: boolean;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}

/**
 * EventPicker — combobox over EventCatalog entries.
 *
 * Promoted to @niuulabs/ui because Tyr's WorkflowBuilder will need it too.
 * Built here in NIU-673 (Ravn personas page).
 */
export function EventPicker({
  value,
  onChange,
  catalog,
  allowNew = false,
  allowEmpty = false,
  placeholder = 'Pick an event…',
  disabled,
  className,
}: EventPickerProps) {
  const listboxId = useId();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');

  const names = catalog.map((e) => e.name);
  const filtered = names.filter((n) => n.toLowerCase().includes(query.toLowerCase()));
  const showNew = allowNew && query.trim() && !names.includes(query.trim());
  const showEmpty = allowEmpty && value !== '';

  const handleOpen = useCallback(() => {
    setQuery('');
    setOpen(true);
  }, []);

  const handleClose = useCallback(() => {
    setOpen(false);
    setQuery('');
  }, []);

  const handleSelect = useCallback(
    (name: string) => {
      onChange(name);
      handleClose();
    },
    [onChange, handleClose],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Escape') {
        handleClose();
        return;
      }
      if (e.key === 'Enter') {
        if (showNew) {
          handleSelect(query.trim());
          return;
        }
        if (open && filtered.length > 0 && filtered[0]) {
          handleSelect(filtered[0]);
        }
      }
      if (e.key === 'ArrowDown' && !open) {
        handleOpen();
      }
    },
    [open, filtered, showNew, query, handleSelect, handleClose, handleOpen],
  );

  const displayValue = open ? query : value;

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Anchor asChild>
        <div className="niuu-event-picker">
          <input
            role="combobox"
            aria-expanded={open}
            aria-autocomplete="list"
            aria-haspopup="listbox"
            aria-controls={open ? listboxId : undefined}
            value={displayValue}
            onChange={(e) => {
              setQuery(e.target.value);
              if (!open) setOpen(true);
            }}
            onFocus={handleOpen}
            onBlur={(e) => {
              if (!e.relatedTarget?.closest('.niuu-event-picker__content')) {
                handleClose();
              }
            }}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            disabled={disabled}
            autoComplete="off"
            className={cn('niuu-form-control niuu-event-picker__input', className)}
          />
        </div>
      </Popover.Anchor>
      <Popover.Portal>
        <Popover.Content
          className="niuu-event-picker__content"
          onOpenAutoFocus={(e) => e.preventDefault()}
          onInteractOutside={handleClose}
          sideOffset={4}
          align="start"
        >
          <ul id={listboxId} role="listbox" className="niuu-event-picker__list">
            {showEmpty && (
              <li
                role="option"
                aria-selected={value === ''}
                className={cn(
                  'niuu-event-picker__option niuu-event-picker__option--empty',
                  value === '' && 'niuu-event-picker__option--selected',
                )}
                onMouseDown={(e) => {
                  e.preventDefault();
                  handleSelect('');
                }}
              >
                <span className="niuu-event-picker__option-label">— none —</span>
              </li>
            )}
            {filtered.map((name) => (
              <li
                key={name}
                role="option"
                aria-selected={name === value}
                className={cn(
                  'niuu-event-picker__option',
                  name === value && 'niuu-event-picker__option--selected',
                )}
                onMouseDown={(e) => {
                  e.preventDefault();
                  handleSelect(name);
                }}
              >
                <span className="niuu-event-picker__option-name">{name}</span>
              </li>
            ))}
            {showNew && (
              <li
                role="option"
                aria-selected={false}
                className="niuu-event-picker__option niuu-event-picker__option--new"
                onMouseDown={(e) => {
                  e.preventDefault();
                  handleSelect(query.trim());
                }}
              >
                <span className="niuu-event-picker__new-badge">+</span>
                <span className="niuu-event-picker__option-name">
                  Create &ldquo;{query.trim()}&rdquo;
                </span>
              </li>
            )}
            {filtered.length === 0 && !showNew && (
              <li role="status" className="niuu-event-picker__empty">
                No events found
              </li>
            )}
          </ul>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
