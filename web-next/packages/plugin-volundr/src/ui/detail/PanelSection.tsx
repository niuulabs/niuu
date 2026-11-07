import type { ReactNode } from 'react';

/** One titled block of the session panel. The accessible name is the title alone. */
export function PanelSection({
  title,
  count,
  children,
}: {
  title: string;
  /** Shown beside the title ("3/5", "2"). */
  count?: string | number;
  children: ReactNode;
}) {
  return (
    <section className="niuu-session-panel__section" aria-label={title}>
      <h2>
        {title}
        {count !== undefined ? <span className="niuu-session-panel__count">{count}</span> : null}
      </h2>
      {children}
    </section>
  );
}
