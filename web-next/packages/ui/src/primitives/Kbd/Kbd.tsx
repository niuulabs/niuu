import type { ReactNode } from 'react';
import { cn } from '../../utils/cn';
import './Kbd.css';

export interface KbdProps {
  children: ReactNode;
  className?: string;
}

export function Kbd({ children, className }: KbdProps) {
  return <kbd className={cn('niuu-kbd', className)}>{children}</kbd>;
}
