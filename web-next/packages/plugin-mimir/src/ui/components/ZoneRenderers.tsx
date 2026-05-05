import { Fragment } from 'react';
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

function KeyFactsZone({ zone, pages, onNavigate }: ZoneRendererProps & { zone: ZoneKeyFacts }) {
  return (
    <ul className="niuu-m-0 niuu-pl-5">
      {zone.items.map((item, i) => {
        const parts = splitWikilinks(item);
        return (
          <li key={i} className="niuu-text-sm niuu-text-text-secondary niuu-py-[2px]">
            {parts.map((part, j) => {
              if (part.kind === 'link') {
                const target = resolveWikilink(part.slug, pages);
                return (
                  <WikilinkPill
                    key={j}
                    slug={part.slug}
                    broken={target.broken}
                    onNavigate={onNavigate}
                  />
                );
              }
              return <Fragment key={j}>{part.value}</Fragment>;
            })}
          </li>
        );
      })}
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
            {rel.note && <span className="niuu-text-text-secondary niuu-ml-2">— {rel.note}</span>}
          </li>
        );
      })}
    </ul>
  );
}

function AssessmentZone({ zone }: { zone: ZoneAssessment }) {
  return <p className="niuu-text-sm niuu-text-text-secondary niuu-m-0">{zone.text}</p>;
}

function TimelineZone({ zone }: { zone: ZoneTimeline }) {
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
          <span className="niuu-text-sm niuu-text-text-secondary">{entry.note}</span>
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
      return <TimelineZone zone={zone} />;
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
