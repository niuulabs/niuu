import type { ReactNode } from 'react';

export interface CollapsibleSectionProps {
  title: string;
  /** Right-aligned count or hint shown beside the title. */
  meta?: ReactNode;
  /** Suffix for the section's test id. */
  testId: string;
  defaultOpen?: boolean;
  children: ReactNode;
}

/**
 * A collapsible rail section.
 *
 * Built on <details>/<summary> so keyboard operation, the disclosure role and
 * screen-reader expanded state come from the element rather than being
 * re-implemented with aria attributes and key handlers.
 */
export function CollapsibleSection({
  title,
  meta,
  testId,
  defaultOpen = true,
  children,
}: CollapsibleSectionProps) {
  return (
    <details
      className="obs-subnav__section"
      data-testid={`subnav-section-${testId}`}
      open={defaultOpen}
    >
      <summary className="obs-subnav__label" data-testid={`subnav-toggle-${testId}`}>
        <span className="obs-subnav__chevron" aria-hidden="true" />
        <span className="obs-subnav__label-text">{title}</span>
        {meta}
      </summary>
      {children}
    </details>
  );
}
