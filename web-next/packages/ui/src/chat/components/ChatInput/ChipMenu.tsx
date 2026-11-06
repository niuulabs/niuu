import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Check, ChevronDown } from 'lucide-react';

export interface ChipMenuItem {
  value: string;
  label: string;
  /** Secondary text on the right of the row. */
  detail?: string;
  checked: boolean;
}

interface ChipMenuProps {
  /** Accessible name of the chip button, e.g. "Model". */
  name: string;
  /** What the chip shows. */
  children: ReactNode;
  heading: string;
  items: readonly ChipMenuItem[];
  onSelect: (value: string) => void;
  disabled?: boolean;
  testId?: string;
}

/** A composer chip that opens a short single-choice menu above itself. */
export function ChipMenu({
  name,
  children,
  heading,
  items,
  onSelect,
  disabled = false,
  testId,
}: ChipMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (rootRef.current?.contains(event.target as Node)) return;
      setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', close);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className="niuu-chat-chip-root" ref={rootRef}>
      <button
        type="button"
        className="niuu-chat-chip"
        aria-label={name}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
        data-testid={testId}
      >
        {children}
        <ChevronDown className="niuu-chat-chip-chevron" aria-hidden="true" />
      </button>
      {open && (
        <div className="niuu-chat-chip-menu" role="menu" id={menuId} aria-label={heading}>
          <div className="niuu-chat-chip-menu-heading">{heading}</div>
          {items.map((item) => (
            <button
              key={item.value}
              type="button"
              role="menuitemradio"
              aria-checked={item.checked}
              className="niuu-chat-chip-menu-item"
              onClick={() => {
                setOpen(false);
                onSelect(item.value);
              }}
            >
              <span className="niuu-chat-chip-menu-check" aria-hidden="true">
                {item.checked && <Check className="niuu-chat-chip-menu-check-icon" />}
              </span>
              <span>{item.label}</span>
              {item.detail && <span className="niuu-chat-chip-menu-detail">{item.detail}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
