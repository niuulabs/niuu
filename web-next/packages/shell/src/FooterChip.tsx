/**
 * FooterChip — shared status chip for the shell footer bar.
 *
 * Used by plugin footers (TyrFooter, RavnFooter, etc.) to render
 * consistent status indicators: `name ● value`.
 *
 * Separators (`│`) are NOT embedded in the chip — render them at the
 * callsite between chips so the last chip never trails a separator.
 */

export interface FooterChipProps {
  name: string;
  state: 'ok' | 'warn' | 'err';
  value: string;
}

export function FooterChip({ name, state, value }: FooterChipProps) {
  return (
    <span className="niuu-shell__footer-chip" data-testid={`footer-chip-${name}`}>
      {name}{' '}
      <span className="niuu-shell__footer-chip-dot" data-state={state}>
        ●
      </span>{' '}
      {value}
    </span>
  );
}

export function FooterChipSep() {
  return <span className="niuu-shell__footer-chip-sep">│</span>;
}
