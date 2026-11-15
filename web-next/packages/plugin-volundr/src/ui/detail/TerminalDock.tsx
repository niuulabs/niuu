import type { ReactNode } from 'react';
import './TerminalDock.css';

/**
 * Lexi's terminal dock: the session's terminals in a full-width panel under the conversation
 * and the right dock, sized by dragging its top edge.
 */
export function TerminalDock({
  height,
  edge,
  children,
  label = 'Terminal',
  testId = 'terminal-dock',
}: {
  /** The dock's height in pixels: the person's, shortened to leave the conversation room. */
  height: number;
  /** The control on the dock's top edge, such as a resizer; it takes none of the height. */
  edge?: ReactNode;
  children: ReactNode;
  /** What the dock holds, for assistive technology (the terminal, a run's workflow). */
  label?: string;
  testId?: string;
}) {
  return (
    <section
      className="forge-terminal-dock"
      aria-label={label}
      data-testid={testId}
      style={{ height: `${height}px` }}
    >
      {edge}
      <div className="forge-terminal-dock__body">{children}</div>
    </section>
  );
}
