import { cn } from '../../utils/cn';
import './Rune.css';

export interface RuneProps {
  glyph: string;
  size?: number;
  muted?: boolean;
  className?: string;
  title?: string;
}

export function Rune({ glyph, size = 18, muted, className, title }: RuneProps) {
  return (
    <span
      className={cn('niuu-rune', muted && 'niuu-rune--muted', className)}
      style={{ fontSize: size }}
      title={title}
      aria-hidden={!title}
    >
      {glyph}
    </span>
  );
}
