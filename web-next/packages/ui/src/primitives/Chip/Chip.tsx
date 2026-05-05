import type { ReactNode } from 'react';
import { cn } from '../../utils/cn';
import './Chip.css';

export type ChipTone = 'default' | 'brand' | 'critical' | 'muted';

export interface ChipProps {
  tone?: ChipTone;
  children: ReactNode;
  title?: string;
  className?: string;
}

export function Chip({ tone = 'default', children, title, className }: ChipProps) {
  return (
    <span className={cn('niuu-chip', `niuu-chip--${tone}`, className)} title={title}>
      {children}
    </span>
  );
}
