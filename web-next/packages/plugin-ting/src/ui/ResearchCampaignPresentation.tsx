import {
  citationToken,
  cx,
  stripHeading,
  type CitationPopoverState,
  type CritiqueVm,
  type ParsedArtifact,
  type SourceVm,
} from './researchCampaignModel';

export function ResearchSection({
  title,
  meta,
  open,
  onToggle,
  actionLabel,
  onAction,
  children,
}: {
  title: string;
  meta?: string;
  open: boolean;
  onToggle: () => void;
  actionLabel?: string;
  onAction?: () => void;
  children: React.ReactNode;
}) {
  return (
    <section className="ting-research-detail__section">
      <div className="ting-research-detail__section-header">
        <button type="button" className="ting-research-detail__section-toggle" onClick={onToggle}>
          <span className="ting-research-detail__section-caret">{open ? '▾' : '▸'}</span>
          <span className="ting-research-detail__section-title">{title}</span>
          {meta ? <span className="ting-research-detail__section-meta">· {meta}</span> : null}
        </button>
        {actionLabel && onAction ? (
          <button type="button" className="ting-research-detail__section-action" onClick={onAction}>
            {actionLabel}
          </button>
        ) : null}
      </div>
      {open ? <div className="ting-research-detail__section-body">{children}</div> : null}
    </section>
  );
}

export function QualityDots({ score }: { score: number }) {
  return (
    <span className="ting-research-detail__quality-dots" aria-label={`quality ${score} of 5`}>
      {[0, 1, 2, 3, 4].map((index) => (
        <span
          key={index}
          className={cx(
            'ting-research-detail__quality-dot',
            index < score && 'is-filled',
            index >= score && 'is-empty',
          )}
        />
      ))}
    </span>
  );
}

export function ConfidenceBadge({ percent, label }: { percent: number; label: 'low' | 'med' | 'high' }) {
  return (
    <div className="ting-research-detail__confidence">
      <div className="ting-research-detail__confidence-bar">
        <span className="ting-research-detail__confidence-fill" style={{ width: `${percent}%` }} />
      </div>
      <span className="ting-research-detail__confidence-text">
        {percent}% · {label.toUpperCase()}
      </span>
    </div>
  );
}

export function InlineCitation({
  label,
  kind,
  isActive,
  onClick,
}: {
  label: string;
  kind: 'source' | 'critique';
  isActive: boolean;
  onClick: (event: React.MouseEvent<HTMLButtonElement>) => void;
}) {
  return (
    <button
      type="button"
      className={cx(
        'ting-research-detail__citation',
        kind === 'source' ? 'is-source' : 'is-critique',
        isActive && 'is-active',
      )}
      onClick={onClick}
    >
      [{label}]
    </button>
  );
}

export function ResearchMarkdown({
  content,
  activeCitation,
  onCitationClick,
  fallbackSourceLabels,
}: {
  content: string;
  activeCitation: CitationPopoverState | null;
  onCitationClick: (citation: CitationPopoverState | null) => void;
  fallbackSourceLabels?: string[];
}) {
  const clean = stripHeading(content);
  const lines = clean.split('\n');
  const elements: React.ReactNode[] = [];
  let paragraphIndex = 0;

  for (let index = 0; index < lines.length;) {
    const line = lines[index] ?? '';
    if (!line.trim()) {
      index += 1;
      continue;
    }
    if (/^##\s+/.test(line)) {
      elements.push(
        <h2 key={`h2-${index}`} className="ting-research-detail__markdown-h2">
          {line.replace(/^##\s+/, '')}
        </h2>,
      );
      index += 1;
      continue;
    }
    if (/^###\s+/.test(line)) {
      elements.push(
        <h3 key={`h3-${index}`} className="ting-research-detail__markdown-h3">
          {line.replace(/^###\s+/, '')}
        </h3>,
      );
      index += 1;
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^[-*]\s+/.test(lines[index] ?? '')) {
        items.push((lines[index] ?? '').replace(/^[-*]\s+/, ''));
        index += 1;
      }
      elements.push(
        <ul key={`ul-${index}`} className="ting-research-detail__markdown-list">
          {items.map((item, itemIndex) => (
            <li key={`${item}-${itemIndex}`}>
              {renderInlineRich(item, activeCitation, onCitationClick)}
            </li>
          ))}
        </ul>,
      );
      continue;
    }
    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\d+\.\s+/.test(lines[index] ?? '')) {
        items.push((lines[index] ?? '').replace(/^\d+\.\s+/, ''));
        index += 1;
      }
      elements.push(
        <ol key={`ol-${index}`} className="ting-research-detail__markdown-ordered-list">
          {items.map((item, itemIndex) => (
            <li key={`${item}-${itemIndex}`}>
              {renderInlineRich(item, activeCitation, onCitationClick)}
            </li>
          ))}
        </ol>,
      );
      continue;
    }
    if (/^\|.+\|$/.test(line.trim()) && /^\|?[-:\s|]+\|?$/.test((lines[index + 1] ?? '').trim())) {
      const headers = splitTableRow(line);
      index += 2;
      const rows: string[][] = [];
      while (index < lines.length && /^\|.+\|$/.test((lines[index] ?? '').trim())) {
        rows.push(splitTableRow(lines[index] ?? ''));
        index += 1;
      }
      elements.push(
        <div key={`table-${index}`} className="ting-research-detail__markdown-table-wrap">
          <table className="ting-research-detail__markdown-table">
            <thead>
              <tr>
                {headers.map((header) => (
                  <th key={header}>{header}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={`row-${rowIndex}`}>
                  {row.map((cell, cellIndex) => (
                    <td key={`${rowIndex}-${cellIndex}`}>
                      {renderInlineRich(cell, activeCitation, onCitationClick)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    const paragraphLines: string[] = [];
    while (index < lines.length && lines[index]?.trim()) {
      paragraphLines.push(lines[index] ?? '');
      index += 1;
    }
    const paragraph = paragraphLines.join(' ');
    const shouldAppendFallback = paragraphIndex === 0 && !/\[[sc]\d+\]/i.test(paragraph);
    elements.push(
      <p key={`p-${paragraphIndex}`} className="ting-research-detail__markdown-p">
        {renderInlineRich(paragraph, activeCitation, onCitationClick)}
        {shouldAppendFallback && fallbackSourceLabels?.length
          ? fallbackSourceLabels.slice(0, 3).map((label) => (
              <span key={label} className="ting-research-detail__markdown-fallback-citation">
                <InlineCitation
                  label={label}
                  kind="source"
                  isActive={activeCitation?.kind === 'source' && activeCitation.key === label}
                  onClick={(event) => {
                    const rect = event.currentTarget.getBoundingClientRect();
                    onCitationClick({
                      kind: 'source',
                      key: label,
                      anchor: `source-${label}`,
                      x: rect.left,
                      y: rect.bottom,
                    });
                  }}
                />
              </span>
            ))
          : null}
      </p>,
    );
    paragraphIndex += 1;
  }

  return <div className="ting-research-detail__markdown">{elements}</div>;
}

export function splitTableRow(row: string): string[] {
  const trimmed = row.trim().replace(/^\|/, '').replace(/\|$/, '');
  return trimmed.split('|').map((cell) => cell.trim());
}

function renderInlineRich(
  text: string,
  activeCitation: CitationPopoverState | null,
  onCitationClick: (citation: CitationPopoverState | null) => void,
): React.ReactNode {
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  const tokenMatcher = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|\[(s|c)\d+\])/g;
  let match: RegExpExecArray | null;
  while ((match = tokenMatcher.exec(text)) !== null) {
    if (match.index > cursor) {
      parts.push(text.slice(cursor, match.index));
    }
    const token = match[0];
    if (token.startsWith('**') && token.endsWith('**')) {
      parts.push(<strong key={`strong-${key++}`}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('`') && token.endsWith('`')) {
      parts.push(
        <code key={`code-${key++}`} className="ting-research-detail__inline-code">
          {token.slice(1, -1)}
        </code>,
      );
    } else if (/^\[[^\]]+\]\([^)]+\)$/.test(token)) {
      const labelEnd = token.indexOf('](');
      const label = token.slice(1, labelEnd);
      const href = token.slice(labelEnd + 2, -1);
      parts.push(
        <a
          key={`link-${key++}`}
          href={href}
          target="_blank"
          rel="noreferrer"
          className="ting-research-detail__markdown-link"
        >
          {label}
        </a>,
      );
    } else {
      const label = token.slice(1, -1);
      const kind = label.startsWith('c') ? 'critique' : 'source';
      const anchor = `${kind}-${label}`;
      parts.push(
        <span key={`citation-${key++}`} className="ting-research-detail__citation-anchor">
          <InlineCitation
            label={label}
            kind={kind}
            isActive={activeCitation?.anchor === anchor}
            onClick={(event) => {
              if (activeCitation?.anchor === anchor) {
                onCitationClick(null);
                return;
              }
              const rect = event.currentTarget.getBoundingClientRect();
              onCitationClick({
                kind,
                key: label,
                anchor,
                x: rect.left,
                y: rect.bottom,
              });
            }}
          />
        </span>,
      );
    }
    cursor = tokenMatcher.lastIndex;
  }
  if (cursor < text.length) {
    parts.push(text.slice(cursor));
  }
  return parts.length === 1 ? parts[0] : parts;
}

export function CitationPopover({
  citation,
  source,
  critique,
  onOpenDrawer,
  onClose,
}: {
  citation: CitationPopoverState;
  source: SourceVm | null;
  critique: CritiqueVm | null;
  onOpenDrawer: () => void;
  onClose: () => void;
}) {
  const isSource = citation.kind === 'source';
  return (
    <div className={cx('ting-research-detail__popover', !isSource && 'is-critique')}>
      <div className="ting-research-detail__popover-eyebrow">
        {isSource ? 'Source' : 'Critique'} · [{citation.key}]
      </div>
      <div className="ting-research-detail__popover-title">
        {isSource ? (source?.title ?? 'Source') : (critique?.claim ?? 'Critique')}
      </div>
      <div className="ting-research-detail__popover-meta">
        {isSource
          ? `${source?.domain ?? 'unknown'} · cited x${source?.citedCount ?? 0} · q${source?.quality ?? 0}/5`
          : `${critique?.severity ?? 'med'} severity · against ${critique?.against ?? 'current thesis'}`}
      </div>
      <div className="ting-research-detail__popover-quote">
        {isSource
          ? (source?.excerpt ?? 'Excerpt from this source supporting the claim would surface here.')
          : (critique?.note ?? 'This critique challenges the current thesis.')}
      </div>
      <div className="ting-research-detail__popover-actions">
        <button type="button" onClick={onOpenDrawer}>
          open
        </button>
        <button type="button" onClick={onClose}>
          close
        </button>
      </div>
    </div>
  );
}
