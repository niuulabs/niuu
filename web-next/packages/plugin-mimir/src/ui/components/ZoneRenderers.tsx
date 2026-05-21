import { Fragment, type ReactNode } from 'react';
import { MarkdownContent } from '@niuulabs/ui';
import { splitWikilinks, resolveWikilink } from '../../domain';
import { WikilinkPill } from './WikilinkPill';
import type {
  Zone,
  ZoneKeyFacts,
  ZoneRelationships,
  ZoneAssessment,
  ZoneTimeline,
} from '../../domain/page';
import type { PageMeta } from '../../domain/page';

interface ZoneRendererProps {
  zone: Zone;
  pages: PageMeta[];
  onNavigate: (path: string) => void;
}

function renderMarkdownInline(text: string): ReactNode {
  const parts: ReactNode[] = [];
  let cursor = 0;
  let key = 0;

  while (cursor < text.length) {
    if (text.startsWith('**', cursor)) {
      const end = text.indexOf('**', cursor + 2);
      if (end !== -1) {
        parts.push(<strong key={key++}>{text.slice(cursor + 2, end)}</strong>);
        cursor = end + 2;
        continue;
      }
    }

    if (text[cursor] === '`') {
      const end = text.indexOf('`', cursor + 1);
      if (end !== -1) {
        parts.push(
          <code key={key++} className="niuu-chat-md-inline-code">
            {text.slice(cursor + 1, end)}
          </code>,
        );
        cursor = end + 1;
        continue;
      }
    }

    if (text[cursor] === '[') {
      const labelEnd = text.indexOf('](', cursor + 1);
      if (labelEnd !== -1) {
        const urlEnd = text.indexOf(')', labelEnd + 2);
        if (urlEnd !== -1) {
          const label = text.slice(cursor + 1, labelEnd);
          const href = text.slice(labelEnd + 2, urlEnd);
          parts.push(
            <a
              key={key++}
              href={href}
              className="niuu-chat-md-link"
              target="_blank"
              rel="noreferrer"
            >
              {label}
            </a>,
          );
          cursor = urlEnd + 1;
          continue;
        }
      }
    }

    const next = findNextInlineToken(text, cursor);
    if (next === cursor) {
      parts.push(text[cursor]);
      cursor += 1;
      continue;
    }
    parts.push(text.slice(cursor, next));
    cursor = next;
  }

  return parts.length === 1 ? parts[0] : parts;
}

function findNextInlineToken(text: string, startAt: number): number {
  const candidates = [
    text.indexOf('**', startAt),
    text.indexOf('`', startAt),
    text.indexOf('[', startAt),
  ].filter((index) => index !== -1);

  if (candidates.length === 0) {
    return text.length;
  }

  return Math.min(...candidates);
}

function RichInlineText({
  text,
  pages,
  onNavigate,
}: {
  text: string;
  pages: PageMeta[];
  onNavigate: (path: string) => void;
}) {
  const parts = splitWikilinks(text);
  return (
    <>
      {parts.map((part, index) => {
        if (part.kind === 'link') {
          const target = resolveWikilink(part.slug, pages);
          return (
            <WikilinkPill
              key={index}
              slug={part.slug}
              broken={target.broken}
              onNavigate={onNavigate}
            />
          );
        }
        return <Fragment key={index}>{renderMarkdownInline(part.value)}</Fragment>;
      })}
    </>
  );
}

function KeyFactsZone({ zone, pages, onNavigate }: ZoneRendererProps & { zone: ZoneKeyFacts }) {
  return (
    <ul className="niuu-m-0 niuu-pl-5">
      {zone.items.map((item, i) => (
        <li key={i} className="niuu-text-sm niuu-text-text-secondary niuu-py-[2px]">
          <RichInlineText text={item} pages={pages} onNavigate={onNavigate} />
        </li>
      ))}
    </ul>
  );
}

function RelationshipsZone({
  zone,
  pages,
  onNavigate,
}: ZoneRendererProps & { zone: ZoneRelationships }) {
  return (
    <ul className="niuu-m-0 niuu-pl-5">
      {zone.items.map((rel, i) => {
        const target = resolveWikilink(rel.slug, pages);
        return (
          <li key={i} className="niuu-text-sm niuu-text-text-secondary niuu-py-[2px]">
            <WikilinkPill slug={rel.slug} broken={target.broken} onNavigate={onNavigate} />
            {rel.note && (
              <span className="niuu-text-text-secondary niuu-ml-2">
                — <RichInlineText text={rel.note} pages={pages} onNavigate={onNavigate} />
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function AssessmentZone({ zone }: { zone: ZoneAssessment }) {
  return (
    <div className="niuu-text-sm niuu-text-text-secondary">
      <MarkdownContent content={zone.text} />
    </div>
  );
}

function TimelineZone({ zone, pages, onNavigate }: ZoneRendererProps & { zone: ZoneTimeline }) {
  const sorted = [...zone.items].sort((a, b) => b.date.localeCompare(a.date));
  return (
    <div>
      {sorted.map((entry, i) => (
        <div
          key={i}
          className="niuu-grid niuu-grid-cols-[100px_1fr] niuu-gap-2 niuu-py-2 niuu-border-b niuu-border-border-subtle last:niuu-border-b-0"
        >
          <span className="niuu-font-mono niuu-text-xs niuu-text-text-muted niuu-pt-[2px]">
            {entry.date}
          </span>
          <span className="niuu-text-sm niuu-text-text-secondary">
            <RichInlineText text={entry.note} pages={pages} onNavigate={onNavigate} />
          </span>
        </div>
      ))}
      {zone.items.length === 0 && (
        <p className="niuu-text-text-muted niuu-text-xs niuu-italic niuu-m-0">
          No timeline entries yet
        </p>
      )}
    </div>
  );
}

export function ZoneBodyReadonly({
  zone,
  allPages,
  onNavigate,
}: {
  zone: Zone;
  allPages: PageMeta[];
  onNavigate: (path: string) => void;
}) {
  switch (zone.kind) {
    case 'key-facts':
      return <KeyFactsZone zone={zone} pages={allPages} onNavigate={onNavigate} />;
    case 'relationships':
      return <RelationshipsZone zone={zone} pages={allPages} onNavigate={onNavigate} />;
    case 'assessment':
      return <AssessmentZone zone={zone} />;
    case 'timeline':
      return <TimelineZone zone={zone} pages={allPages} onNavigate={onNavigate} />;
  }
}

export function zoneToEditableText(zone: Zone): string {
  switch (zone.kind) {
    case 'key-facts':
      return zone.items.join('\n');
    case 'relationships':
      return zone.items.map((r) => `[[${r.slug}]] — ${r.note}`).join('\n');
    case 'assessment':
      return zone.text;
    case 'timeline':
      return zone.items.map((t) => `${t.date}: ${t.note}`).join('\n');
  }
}
