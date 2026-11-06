import { Fragment, type ReactNode } from 'react';

interface SublineItem {
  id: string;
  node: ReactNode;
}

/** The header's second line: its pieces joined by a dot, with absent pieces left out. */
export function Subline({
  items,
  ...rest
}: {
  items: ReadonlyArray<SublineItem | null | false | undefined | ''>;
  'data-testid'?: string;
}) {
  const present = items.filter((item): item is SublineItem => Boolean(item));
  return (
    <div className="niuu-live-session__subline" {...rest}>
      {present.map((item, index) => (
        <Fragment key={item.id}>
          {index > 0 ? (
            <span className="niuu-live-session__sep" aria-hidden="true">
              ·
            </span>
          ) : null}
          {item.node}
        </Fragment>
      ))}
    </div>
  );
}
