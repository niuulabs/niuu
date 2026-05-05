import * as RadixPopover from '@radix-ui/react-popover';
import type { ReactNode } from 'react';
import { cn } from '../../utils/cn';
import './Popover.css';

export interface PopoverProps {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: ReactNode;
}

export interface PopoverContentProps {
  children: ReactNode;
  side?: 'top' | 'right' | 'bottom' | 'left';
  align?: 'start' | 'center' | 'end';
  sideOffset?: number;
  className?: string;
}

const DEFAULT_SIDE_OFFSET = 6;

export function Popover({ open, onOpenChange, children }: PopoverProps) {
  return (
    <RadixPopover.Root open={open} onOpenChange={onOpenChange}>
      {children}
    </RadixPopover.Root>
  );
}

export const PopoverTrigger = RadixPopover.Trigger;
export const PopoverClose = RadixPopover.Close;

export function PopoverContent({
  children,
  side = 'bottom',
  align = 'center',
  sideOffset = DEFAULT_SIDE_OFFSET,
  className,
}: PopoverContentProps) {
  return (
    <RadixPopover.Portal>
      <RadixPopover.Content
        className={cn('niuu-popover-content', className)}
        side={side}
        align={align}
        sideOffset={sideOffset}
      >
        {children}
        <RadixPopover.Arrow className="niuu-popover-arrow" />
      </RadixPopover.Content>
    </RadixPopover.Portal>
  );
}
