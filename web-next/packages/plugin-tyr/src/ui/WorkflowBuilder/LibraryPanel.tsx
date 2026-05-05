/**
 * LibraryPanel — block palette + categorised persona library.
 *
 * Sits on the left side of the canvas (between the templates sidebar and
 * the graph). Contains structural block types (Stage, Condition, Human gate,
 * End) and categorised persona entries grouped by role.
 *
 * Owner: plugin-tyr (WorkflowBuilder).
 */

import { useState } from 'react';
import { cn } from '@niuulabs/ui';
import {
  EPHEMERAL_LOCAL_MOUNT,
  MIMIR_MOUNT_MIME,
  serializeWorkflowRegistryMount,
  type WorkflowRegistryMount,
} from './mimirRegistry';

export interface PersonaEntry {
  id: string;
  label: string;
  role: string;
  summary?: string;
  consumes?: string[];
  produces?: string[];
}

export interface LibraryPanelProps {
  personas: PersonaEntry[];
  registryMounts?: WorkflowRegistryMount[];
}

// Default mock persona library — used when no personas are passed.
export const DEFAULT_PERSONAS: PersonaEntry[] = [
  {
    id: 'persona-decomposer',
    label: 'decomposer',
    role: 'plan',
    summary: 'Breaks a brief into concrete raids.',
    consumes: ['brief', 'tracker.issue'],
    produces: ['raid.plan'],
  },
  {
    id: 'persona-investigator',
    label: 'investigator',
    role: 'plan',
    summary: 'Pulls repository and prior-art context.',
    consumes: ['brief', 'repo.index'],
    produces: ['context.bundle'],
  },
  {
    id: 'persona-coding-agent',
    label: 'coding-agent',
    role: 'build',
    summary: 'Implements product changes.',
    consumes: ['raid.plan', 'context.bundle'],
    produces: ['code.changed'],
  },
  {
    id: 'persona-coder',
    label: 'coder',
    role: 'build',
    summary: 'Focused code generation worker.',
    consumes: ['raid.plan'],
    produces: ['patch.ready'],
  },
  {
    id: 'persona-raid-executor',
    label: 'raid-executor',
    role: 'build',
    summary: 'Runs a full raid execution loop.',
    consumes: ['dispatch.order'],
    produces: ['raid.completed'],
  },
  {
    id: 'persona-qa',
    label: 'qa-agent',
    role: 'verify',
    summary: 'Runs integration and acceptance checks.',
    consumes: ['code.changed'],
    produces: ['qa.report'],
  },
  {
    id: 'persona-verifier',
    label: 'verifier',
    role: 'verify',
    summary: 'Collects evidence and traces.',
    consumes: ['artifact.bundle'],
    produces: ['evidence.pack'],
  },
  {
    id: 'persona-reviewer',
    label: 'reviewer',
    role: 'review',
    summary: 'Performs human-like release review.',
    consumes: ['qa.report', 'patch.ready'],
    produces: ['review.verdict'],
  },
  {
    id: 'persona-gatekeeper',
    label: 'gatekeeper',
    role: 'gate',
    summary: 'Approves a release handoff gate.',
    consumes: ['review.verdict'],
    produces: ['gate.approved'],
  },
];

const FLOW_CONTROL_BLOCKS = [
  { id: 'stage', label: 'Stage', glyph: '◆' },
  { id: 'cond', label: 'Condition', glyph: '?' },
  { id: 'gate', label: 'Human gate', glyph: '⌘' },
  { id: 'end', label: 'End', glyph: '●' },
];

function groupByRole(personas: PersonaEntry[]): [string, PersonaEntry[]][] {
  const groups = new Map<string, PersonaEntry[]>();
  for (const p of personas) {
    const list = groups.get(p.role) ?? [];
    list.push(p);
    groups.set(p.role, list);
  }
  return [...groups.entries()];
}

function personaGlyph(role: string) {
  switch (role) {
    case 'plan':
      return { shape: 'dashed-circle', text: 'D' };
    case 'build':
      return { shape: 'square', text: 'C' };
    case 'verify':
      return { shape: 'triangle', text: 'V' };
    case 'gate':
      return { shape: 'hex', text: 'I' };
    default:
      return { shape: 'square', text: role.slice(0, 1).toUpperCase() };
  }
}

export function LibraryPanel({ personas, registryMounts = [] }: LibraryPanelProps) {
  const [search, setSearch] = useState('');
  const resourceMounts = [EPHEMERAL_LOCAL_MOUNT, ...registryMounts];
  const filtered = search
    ? personas.filter(
        (p) =>
          p.label.toLowerCase().includes(search.toLowerCase()) ||
          p.role.toLowerCase().includes(search.toLowerCase()),
      )
    : personas;
  const filteredMounts = search
    ? resourceMounts.filter(
        (mount) =>
          mount.name.toLowerCase().includes(search.toLowerCase()) ||
          mount.role.toLowerCase().includes(search.toLowerCase()) ||
          (mount.categories ?? []).some((category) =>
            category.toLowerCase().includes(search.toLowerCase()),
          ),
      )
    : resourceMounts;
  const groups = groupByRole(filtered);

  return (
    <div
      data-testid="library-panel"
      className="niuu-w-[212px] niuu-shrink-0 niuu-border-r niuu-border-border niuu-bg-bg-secondary niuu-flex niuu-flex-col niuu-overflow-hidden"
    >
      {/* Header */}
      <div className="niuu-flex niuu-items-start niuu-justify-between niuu-px-4 niuu-pt-3 niuu-pb-3 niuu-border-b niuu-border-border">
        <div className="niuu-flex niuu-flex-col niuu-gap-0.5">
          <span className="niuu-text-[12px] niuu-font-semibold niuu-text-text-primary niuu-font-sans">
            Library
          </span>
        </div>
        <button
          type="button"
          className="niuu-bg-transparent niuu-border-none niuu-text-text-muted niuu-cursor-pointer niuu-text-xs niuu-p-0 hover:niuu-text-text-secondary"
        >
          +
        </button>
      </div>

      {/* Search */}
      <div className="niuu-px-4 niuu-py-3 niuu-border-b niuu-border-border">
        <input
          type="text"
          placeholder="Search actors, resources, and mounts..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          data-testid="library-search"
          className="niuu-w-full niuu-py-2.5 niuu-px-3.5 niuu-bg-bg-tertiary niuu-border niuu-border-solid niuu-border-border-subtle niuu-rounded-lg niuu-text-text-secondary niuu-font-sans niuu-text-[11px] niuu-outline-none niuu-box-border"
        />
      </div>

      {/* Scrollable content */}
      <div className="niuu-flex-1 niuu-overflow-y-auto niuu-px-4 niuu-pb-4">
        {/* Flow control */}
        {!search && (
          <>
            <div className="niuu-text-[9px] niuu-font-semibold niuu-uppercase niuu-tracking-[0.22em] niuu-text-text-faint niuu-font-mono niuu-mb-2 niuu-mt-3">
              FLOW CONTROL
            </div>
            <div className="niuu-flex niuu-flex-col niuu-gap-1.5 niuu-mb-4">
              {FLOW_CONTROL_BLOCKS.map((b) => (
                <div
                  key={b.id}
                  draggable
                  onDragStart={(e) => {
                    e.dataTransfer.setData('application/niuu-node-kind', b.id);
                    e.dataTransfer.effectAllowed = 'copy';
                  }}
                  className="niuu-py-2.5 niuu-px-3.5 niuu-bg-bg-elevated niuu-rounded-lg niuu-border niuu-border-border-subtle niuu-cursor-grab niuu-text-xs niuu-text-text-primary niuu-font-sans niuu-select-none niuu-flex niuu-items-center niuu-gap-2.5"
                >
                  <span className="niuu-text-[13px] niuu-text-text-primary">{b.glyph}</span>
                  <span className="niuu-font-semibold niuu-text-[11px]">{b.label}</span>
                </div>
              ))}
            </div>
          </>
        )}

        {filteredMounts.length > 0 && (
          <>
            <div className="niuu-text-[9px] niuu-font-semibold niuu-uppercase niuu-tracking-[0.22em] niuu-text-text-faint niuu-font-mono niuu-mb-2 niuu-mt-3">
              RESOURCES
            </div>
            <div className="niuu-flex niuu-flex-col niuu-gap-1.5 niuu-mb-4">
              {filteredMounts.map((mount) => (
                <div
                  key={mount.id}
                  data-testid={`mimir-mount-${mount.id}`}
                  draggable
                  onDragStart={(e) => {
                    e.dataTransfer.setData(
                      MIMIR_MOUNT_MIME,
                      serializeWorkflowRegistryMount(mount),
                    );
                    e.dataTransfer.effectAllowed = 'copy';
                  }}
                  className={cn(
                    'niuu-py-2.5 niuu-px-3 niuu-bg-bg-elevated niuu-rounded-lg niuu-border niuu-cursor-grab niuu-text-xs niuu-text-text-primary niuu-font-sans niuu-select-none niuu-flex niuu-items-start niuu-gap-2.5',
                    mount.lifecycle === 'ephemeral'
                      ? 'niuu-border-status-emerald/40'
                      : 'niuu-border-border-subtle',
                  )}
                >
                  <div
                    className={cn(
                      'niuu-flex niuu-h-7 niuu-w-7 niuu-items-center niuu-justify-center niuu-font-mono niuu-text-[11px]',
                      mount.lifecycle === 'ephemeral'
                        ? 'niuu-rounded-full niuu-border niuu-border-status-emerald/60 niuu-text-status-emerald'
                        : 'niuu-rounded-md niuu-border niuu-border-brand/60 niuu-text-brand',
                    )}
                  >
                    {mount.lifecycle === 'ephemeral' ? 'E' : 'M'}
                  </div>
                  <div className="niuu-flex niuu-flex-col niuu-leading-tight niuu-min-w-0 niuu-flex-1">
                    <span className="niuu-text-text-primary niuu-font-semibold niuu-truncate niuu-text-[11px]">
                      {mount.name}
                    </span>
                    <span className="niuu-text-[9px] niuu-text-text-faint niuu-font-mono">
                      {mount.lifecycle === 'ephemeral'
                        ? 'workspace-local scratch'
                        : `${mount.role} · ${mount.kind}`}
                      {(mount.categories ?? []).length > 0
                        ? ` · ${(mount.categories ?? []).slice(0, 2).join(', ')}`
                        : ''}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}

        {/* Persona groups */}
        {groups.map(([role, entries]) => (
          <div key={role}>
            <div className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-2 niuu-mb-1.5 niuu-mt-4 niuu-px-0.5">
              <div className="niuu-flex niuu-flex-col niuu-gap-0.5">
                <span className="niuu-text-[9px] niuu-font-semibold niuu-uppercase niuu-tracking-[0.22em] niuu-text-text-faint niuu-font-mono">
                  ACTORS · {role}
                </span>
              </div>
              <span className="niuu-text-[9px] niuu-text-text-faint niuu-font-mono niuu-mt-0.5">
                {entries.length}
              </span>
            </div>
            <div className="niuu-flex niuu-flex-col niuu-gap-1.5">
              {entries.map((persona) => (
                <div
                  key={persona.id}
                  data-testid={`persona-chip-${persona.id}`}
                  draggable
                  onDragStart={(e) => {
                    e.dataTransfer.setData('application/niuu-persona-id', persona.id);
                    e.dataTransfer.effectAllowed = 'copy';
                  }}
                  className="niuu-py-2.5 niuu-px-3 niuu-bg-bg-elevated niuu-rounded-lg niuu-border niuu-border-border-subtle niuu-cursor-grab niuu-text-xs niuu-text-text-primary niuu-font-sans niuu-select-none niuu-flex niuu-items-start niuu-gap-2.5"
                >
                  {(() => {
                    const glyph = personaGlyph(persona.role);
                    if (glyph.shape === 'dashed-circle') {
                      return (
                        <div className="niuu-flex niuu-h-7 niuu-w-7 niuu-items-center niuu-justify-center niuu-rounded-full niuu-border niuu-border-dashed niuu-border-[#aeddff] niuu-text-[#d6efff] niuu-font-mono niuu-text-[11px]">
                          {glyph.text}
                        </div>
                      );
                    }
                    if (glyph.shape === 'triangle') {
                      return (
                        <div className="niuu-relative niuu-h-7 niuu-w-7">
                          <div className="niuu-absolute niuu-inset-0 niuu-flex niuu-items-center niuu-justify-center niuu-text-[#d6efff] niuu-font-mono niuu-text-[11px]">
                            △
                          </div>
                          <div className="niuu-absolute niuu-inset-0 niuu-flex niuu-items-center niuu-justify-center niuu-text-[#d6efff] niuu-font-mono niuu-text-[8px]">
                            {glyph.text}
                          </div>
                        </div>
                      );
                    }
                    return (
                      <div className="niuu-flex niuu-h-7 niuu-w-7 niuu-items-center niuu-justify-center niuu-rounded-md niuu-border niuu-border-[#aeddff] niuu-text-[#d6efff] niuu-font-mono niuu-text-[11px]">
                        {glyph.text}
                      </div>
                    );
                  })()}
                  <div className="niuu-flex niuu-flex-col niuu-leading-tight niuu-min-w-0 niuu-flex-1">
                    <span className="niuu-text-text-primary niuu-font-semibold niuu-truncate niuu-text-[11px]">
                      {persona.label}
                    </span>
                    <span className="niuu-text-[9px] niuu-text-text-faint niuu-font-mono">
                      {persona.role}
                    </span>
                  </div>
                  <span className="niuu-ml-auto niuu-text-text-faint niuu-text-[9px] niuu-font-mono niuu-mt-0.5">
                    ⇆
                  </span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
