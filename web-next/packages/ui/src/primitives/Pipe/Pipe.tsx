import { cn } from '../../utils/cn';
import './Pipe.css';

export type PipeCellStatus = 'ok' | 'run' | 'warn' | 'crit' | 'gate' | 'pend';

export interface PipeCell {
  status: PipeCellStatus;
  label?: string;
}

export interface PipeProps {
  cells: PipeCell[];
  cellWidth?: number;
  className?: string;
}

export function Pipe({ cells, cellWidth = 18, className }: PipeProps) {
  return (
    <span className={cn('niuu-pipe', className)} role="list" aria-label="phase progress">
      {cells.map((cell, i) => (
        <span
          key={i}
          className={cn('niuu-pipe__cell', `niuu-pipe__cell--${cell.status}`)}
          style={{ width: cellWidth }}
          role="listitem"
          title={cell.label}
          aria-label={cell.label ?? cell.status}
        />
      ))}
    </span>
  );
}
