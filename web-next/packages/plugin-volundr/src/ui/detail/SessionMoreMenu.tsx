import { useState } from 'react';
import { MoreHorizontal, type LucideIcon } from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger, cn } from '@niuulabs/ui';
import './detail.css';

export interface MoreMenuItem {
  id: string;
  label: string;
  icon: LucideIcon;
  onSelect: () => void;
  disabled?: boolean;
  tone?: 'critical';
  /** Rule above this item, to set apart a group (the destructive ones). */
  separated?: boolean;
  /** A keyboard shortcut shown at the item's end. */
  shortcut?: string;
}

/** The button that opens the menu; the header's "More" when none is given. */
export interface MoreMenuTrigger {
  icon: LucideIcon;
  label: string;
  className: string;
  testId?: string;
}

const MORE_TRIGGER: MoreMenuTrigger = {
  icon: MoreHorizontal,
  label: 'More actions',
  className: 'niuu-live-session__toolbar-button niuu-live-session__toolbar-button--neutral',
  testId: 'session-more-menu',
};

/** The header's secondary actions. Nothing renders when there are none. */
export function SessionMoreMenu({
  items,
  trigger = MORE_TRIGGER,
}: {
  items: readonly MoreMenuItem[];
  trigger?: MoreMenuTrigger;
}) {
  const TriggerIcon = trigger.icon;
  const [open, setOpen] = useState(false);
  if (items.length === 0) return null;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={trigger.className}
          aria-label={trigger.label}
          title={trigger === MORE_TRIGGER ? 'More' : trigger.label}
          data-testid={trigger.testId}
        >
          <TriggerIcon className="niuu-live-session__toolbar-icon" aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="forge-more-menu">
        {items.map((item) => {
          const Icon = item.icon;
          return (
            <div key={item.id} role="presentation">
              {item.separated ? <hr className="forge-more-menu__rule" /> : null}
              <button
                type="button"
                className={cn(
                  'forge-more-menu__item',
                  item.tone === 'critical' && 'forge-more-menu__item--critical',
                )}
                disabled={item.disabled}
                onClick={() => {
                  setOpen(false);
                  item.onSelect();
                }}
              >
                <Icon aria-hidden="true" />
                {item.label}
                {item.shortcut ? (
                  <kbd className="forge-more-menu__shortcut">{item.shortcut}</kbd>
                ) : null}
              </button>
            </div>
          );
        })}
      </PopoverContent>
    </Popover>
  );
}
