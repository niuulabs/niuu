import { cn } from '../../../utils/cn';
import type { OutcomeTone } from '../../workers';
import './VerdictPill.css';

/** What a pill can read as: what a worker concluded, or that it is working or waiting. */
export type VerdictPillTone = OutcomeTone | 'working' | 'waiting';

export interface VerdictPillProps {
  label: string;
  tone: VerdictPillTone;
  className?: string;
}

/** A worker's state as a small coloured pill: pass is ok, changes warn, blocked critical. */
export function VerdictPill({ label, tone, className }: VerdictPillProps) {
  return (
    <span
      className={cn('niuu-chat-verdict', `niuu-chat-verdict--${tone}`, className)}
      data-tone={tone}
    >
      {label}
    </span>
  );
}
