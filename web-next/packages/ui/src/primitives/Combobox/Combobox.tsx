import { useState, useCallback, useRef, useId } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { cn } from '../../utils/cn';
import { useField } from '../Field';
import type { Option } from '../option';

export type ComboboxOption = Option;

export interface ComboboxProps {
  options: ComboboxOption[];
  value?: string;
  onValueChange?: (value: string) => void;
  placeholder?: string;
  emptyMessage?: string;
  disabled?: boolean;
  name?: string;
  className?: string;
}

export function Combobox({
  options,
  value,
  onValueChange,
  placeholder = 'Search…',
  emptyMessage = 'No options found',
  disabled,
  name,
  className,
}: ComboboxProps) {
  const { id: fieldId, hintId, errorId, hasError } = useField();
  const listboxId = useId();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const selectedLabel = options.find((o) => o.value === value)?.label ?? '';

  const filtered = options.filter((o) => o.label.toLowerCase().includes(query.toLowerCase()));

  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined;

  const handleOpen = useCallback(() => {
    setQuery('');
    setOpen(true);
  }, []);

  const handleClose = useCallback(() => {
    setOpen(false);
    setQuery('');
  }, []);

  const handleSelect = useCallback(
    (option: ComboboxOption) => {
      if (option.disabled) return;
      onValueChange?.(option.value);
      handleClose();
      inputRef.current?.focus();
    },
    [onValueChange, handleClose],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Escape') {
        handleClose();
        return;
      }
      if (e.key === 'Enter' && open && filtered.length > 0) {
        const first = filtered.find((o) => !o.disabled);
        if (first) handleSelect(first);
        return;
      }
      if (e.key === 'ArrowDown' && !open) {
        handleOpen();
      }
    },
    [open, filtered, handleSelect, handleClose, handleOpen],
  );

  const displayValue = open ? query : selectedLabel;

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Anchor asChild>
        <div className="niuu-combobox">
          <input
            ref={inputRef}
            id={fieldId}
            name={name}
            role="combobox"
            aria-expanded={open}
            aria-autocomplete="list"
            aria-haspopup="listbox"
            aria-controls={open ? listboxId : undefined}
            aria-invalid={hasError || undefined}
            aria-describedby={describedBy}
            value={displayValue}
            onChange={(e) => {
              setQuery(e.target.value);
              if (!open) setOpen(true);
            }}
            onFocus={handleOpen}
            onBlur={(e) => {
              if (!e.relatedTarget?.closest('.niuu-combobox__content')) {
                handleClose();
              }
            }}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            disabled={disabled}
            autoComplete="off"
            className={cn(
              'niuu-form-control niuu-combobox__input',
              hasError && 'niuu-form-control--error niuu-combobox__input--error',
              className,
            )}
          />
        </div>
      </Popover.Anchor>
      <Popover.Portal>
        <Popover.Content
          className="niuu-combobox__content"
          onOpenAutoFocus={(e) => e.preventDefault()}
          onInteractOutside={handleClose}
          sideOffset={4}
          align="start"
        >
          <ul id={listboxId} role="listbox" className="niuu-combobox__list">
            {filtered.length === 0 ? (
              <li className="niuu-combobox__empty" role="status">
                {emptyMessage}
              </li>
            ) : (
              filtered.map((option) => (
                <li
                  key={option.value}
                  role="option"
                  aria-selected={option.value === value}
                  aria-disabled={option.disabled}
                  className={cn(
                    'niuu-combobox__option',
                    option.value === value && 'niuu-combobox__option--selected',
                    option.disabled && 'niuu-combobox__option--disabled',
                  )}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    handleSelect(option);
                  }}
                >
                  {option.label}
                </li>
              ))
            )}
          </ul>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
