import * as RadixTooltip from '@radix-ui/react-tooltip';
import type { ReactNode } from 'react';
import { cn } from '../../utils/cn';
import './Tooltip.css';

export { TooltipProvider } from '@radix-ui/react-tooltip';

const DEFAULT_DELAY_MS = 400;
const DEFAULT_SIDE_OFFSET = 5;

export interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
  side?: 'top' | 'right' | 'bottom' | 'left';
  delayMs?: number;
  className?: string;
}

export function Tooltip({
  content,
  children,
  side = 'top',
  delayMs = DEFAULT_DELAY_MS,
  className,
}: TooltipProps) {
  return (
    <RadixTooltip.Root delayDuration={delayMs}>
      <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
      <RadixTooltip.Portal>
        <RadixTooltip.Content
          className={cn('niuu-tooltip-content', className)}
          side={side}
          sideOffset={DEFAULT_SIDE_OFFSET}
        >
          {content}
          <RadixTooltip.Arrow className="niuu-tooltip-arrow" />
        </RadixTooltip.Content>
      </RadixTooltip.Portal>
    </RadixTooltip.Root>
  );
}
