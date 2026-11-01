/**
 * LibraryPanel — contextual step and resource picker.
 *
 * Owner: plugin-ting (WorkflowBuilder).
 */

import { useEffect, useRef, useState } from 'react';
import { cn } from '@niuulabs/ui';
import {
  EPHEMERAL_LOCAL_MOUNT,
  MIMIR_MOUNT_MIME,
  serializeWorkflowRegistryMount,
  type WorkflowRegistryMount,
} from './mimirRegistry';
import type { WorkflowNodeKind } from '../../domain/workflow';

export interface PersonaEntry {
  id: string;
  label: string;
  role: string;
  summary?: string;
  consumes?: string[];
  produces?: string[];
  /** Outcome name to event type, when the persona publishes distinct results. */
  outcomeEvents?: Readonly<Record<string, string>>;
}

export interface LibraryPanelProps {
  /** Embedded inside a positioned canvas picker instead of the library drawer. */
  embedded?: boolean;
  personas: PersonaEntry[];
  registryMounts?: WorkflowRegistryMount[];
  /** Closes the panel. Omit to render without a close control. */
  onClose?: () => void;
  onAddNode?: (kind: WorkflowNodeKind) => void;
  onAddPersona?: (personaId: string) => void;
  onAddMimirResource?: (mount: WorkflowRegistryMount) => void;
}

// Default mock persona library — used when no personas are passed.
export const DEFAULT_PERSONAS: PersonaEntry[] = [
  {
    id: 'coder',
    label: 'coder',
    role: 'build',
    summary: 'Implements the requested code change.',
    consumes: ['code.requested', 'review.changes_requested'],
    produces: ['code.changed'],
  },
  {
    id: 'reviewer',
    label: 'reviewer',
    role: 'review',
    summary: 'Reviews the code change and requests revisions when needed.',
    consumes: ['code.changed'],
    produces: ['review.completed'],
  },
  {
    id: 'security-auditor',
    label: 'security-auditor',
    role: 'review',
    summary: 'Audits the same code change in parallel for security issues.',
    consumes: ['code.changed'],
    produces: ['security.completed'],
  },
  {
    id: 'postmortem-analyst',
    label: 'postmortem-analyst',
    role: 'memory',
    summary: 'Synthesizes a post-mortem source after review fan-in completes.',
    consumes: ['review.completed', 'security.completed'],
    produces: ['mimir.source.ingested'],
  },
  {
    id: 'mimir-memory-curator',
    label: 'mimir-memory-curator',
    role: 'memory',
    summary: 'Curates ingested memory into canonical Mimir wiki pages.',
    consumes: ['mimir.source.ingested'],
    produces: ['mimir.curated'],
  },
];

const FLOW_CONTROL_BLOCKS: ReadonlyArray<{
  id: WorkflowNodeKind;
  label: string;
  glyph: string;
  summary: string;
}> = [
  { id: 'trigger', label: 'Trigger', glyph: '↗', summary: 'Start from an event or manual request' },
  { id: 'stage', label: 'Stage', glyph: '◆', summary: 'Group one or more actors' },
  { id: 'cond', label: 'Condition', glyph: '?', summary: 'Branch on a typed predicate' },
  { id: 'gate', label: 'Human gate', glyph: '⌘', summary: 'Wait for approval or evidence' },
  { id: 'wait', label: 'External wait', glyph: '◌', summary: 'Resume on an external event' },
  {
    id: 'subworkflow',
    label: 'Child workflow',
    glyph: '↳',
    summary: 'Run a pinned child definition',
  },
  {
    id: 'include',
    label: 'Include',
    glyph: '⧉',
    summary: 'Inline stages from a pinned workflow',
  },
  { id: 'end', label: 'End', glyph: '●', summary: 'Handle an outcome explicitly' },
  { id: 'resource', label: 'Resource', glyph: '▤', summary: 'Bind a workflow resource' },
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

export function LibraryPanel({
  embedded = false,
  personas,
  registryMounts = [],
  onClose,
  onAddNode,
  onAddPersona,
  onAddMimirResource,
}: LibraryPanelProps) {
  const [search, setSearch] = useState('');
  const searchRef = useRef<HTMLInputElement>(null);
  // Command-palette convention: focus search the moment the panel opens.
  useEffect(() => {
    searchRef.current?.focus();
  }, []);
  const resourceMounts =
    embedded && !onAddMimirResource ? [] : [EPHEMERAL_LOCAL_MOUNT, ...registryMounts];
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
  const normalizedSearch = search.trim().toLowerCase();
  const filteredBlocks = normalizedSearch
    ? FLOW_CONTROL_BLOCKS.filter((block) =>
        `${block.label} ${block.summary}`.toLowerCase().includes(normalizedSearch),
      )
    : FLOW_CONTROL_BLOCKS;

  return (
    <div
      data-testid="library-panel"
      className={cn(
        'niuu:flex niuu:flex-col niuu:overflow-hidden niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-secondary/95 niuu:shadow-2xl niuu:backdrop-blur-md',
        embedded
          ? 'niuu:h-full niuu:w-full'
          : 'niuu:absolute niuu:inset-y-3 niuu:left-3 niuu:z-10 niuu:w-[280px]',
      )}
    >
      {/* Header */}
      <div className="niuu:flex niuu:items-start niuu:justify-between niuu:px-4 niuu:pt-3 niuu:pb-3 niuu:border-b niuu:border-border">
        <div className="niuu:flex niuu:flex-col niuu:gap-0.5">
          <span className="niuu:text-[12px] niuu:font-semibold niuu:text-text-primary niuu:font-sans">
            Add node
          </span>
          <span className="niuu:text-[9px] niuu:font-mono niuu:text-text-faint">
            Add, drag, or search
          </span>
        </div>
        {onClose && (
          <button
            type="button"
            data-testid="library-panel-close"
            onClick={onClose}
            aria-label="Close node picker"
            title="Close node picker"
            className="niuu:bg-transparent niuu:border-none niuu:text-text-muted niuu:cursor-pointer niuu:text-[15px] niuu:leading-none niuu:p-1 niuu:rounded-md niuu:hover:bg-bg-tertiary niuu:hover:text-text-primary"
          >
            ×
          </button>
        )}
      </div>

      {/* Search */}
      <div className="niuu:px-4 niuu:py-3 niuu:border-b niuu:border-border">
        <input
          ref={searchRef}
          type="text"
          placeholder="Search nodes, actors, and resources..."
          aria-label="Search nodes, actors, and resources"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') onClose?.();
          }}
          data-testid="library-search"
          className="niuu:w-full niuu:py-2.5 niuu:px-3.5 niuu:bg-bg-tertiary niuu:border niuu:border-solid niuu:border-border-subtle niuu:rounded-lg niuu:text-text-secondary niuu:font-sans niuu:text-[11px] niuu:outline-none niuu:box-border"
        />
      </div>

      {/* Scrollable content */}
      <div className="niuu:flex-1 niuu:overflow-y-auto niuu:px-4 niuu:pb-4">
        {/* Flow control */}
        {filteredBlocks.length > 0 && (
          <>
            <div className="niuu:text-[9px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.22em] niuu:text-text-faint niuu:font-mono niuu:mb-2 niuu:mt-3">
              FLOW CONTROL
            </div>
            <div className="niuu:flex niuu:flex-col niuu:gap-1.5 niuu:mb-4">
              {filteredBlocks.map((b) => (
                <button
                  type="button"
                  key={b.id}
                  draggable
                  data-testid={`library-add-${b.id}`}
                  onClick={() => {
                    onAddNode?.(b.id);
                    if (onAddNode) onClose?.();
                  }}
                  onDragStart={(e) => {
                    e.dataTransfer.setData('application/niuu-node-kind', b.id);
                    e.dataTransfer.effectAllowed = 'copy';
                  }}
                  className="niuu:w-full niuu:py-2.5 niuu:px-3.5 niuu:bg-bg-elevated niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:cursor-grab niuu:text-xs niuu:text-text-primary niuu:font-sans niuu:select-none niuu:flex niuu:items-center niuu:gap-2.5 niuu:text-left niuu:hover:border-border"
                >
                  <span className="niuu:text-[13px] niuu:text-text-primary">{b.glyph}</span>
                  <span className="niuu:flex niuu:min-w-0 niuu:flex-col">
                    <span className="niuu:font-semibold niuu:text-[11px]">{b.label}</span>
                    <span className="niuu:text-[9px] niuu:text-text-faint">{b.summary}</span>
                  </span>
                </button>
              ))}
            </div>
          </>
        )}

        {filteredMounts.length > 0 && (
          <>
            <div className="niuu:text-[9px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.22em] niuu:text-text-faint niuu:font-mono niuu:mb-2 niuu:mt-3">
              RESOURCES
            </div>
            <div className="niuu:flex niuu:flex-col niuu:gap-1.5 niuu:mb-4">
              {filteredMounts.map((mount) => (
                <button
                  type="button"
                  key={mount.id}
                  data-testid={`mimir-mount-${mount.id}`}
                  draggable
                  onClick={() => {
                    onAddMimirResource?.(mount);
                    if (onAddMimirResource) onClose?.();
                  }}
                  onDragStart={(e) => {
                    e.dataTransfer.setData(MIMIR_MOUNT_MIME, serializeWorkflowRegistryMount(mount));
                    e.dataTransfer.effectAllowed = 'copy';
                  }}
                  className={cn(
                    'niuu:w-full niuu:py-2.5 niuu:px-3 niuu:bg-bg-elevated niuu:rounded-lg niuu:border niuu:cursor-grab niuu:text-xs niuu:text-text-primary niuu:font-sans niuu:select-none niuu:flex niuu:items-start niuu:gap-2.5 niuu:text-left',
                    mount.lifecycle === 'ephemeral'
                      ? 'niuu:border-status-emerald/40'
                      : 'niuu:border-border-subtle',
                  )}
                >
                  <div
                    className={cn(
                      'niuu:flex niuu:h-7 niuu:w-7 niuu:items-center niuu:justify-center niuu:font-mono niuu:text-[11px]',
                      mount.lifecycle === 'ephemeral'
                        ? 'niuu:rounded-full niuu:border niuu:border-status-emerald/60 niuu:text-status-emerald'
                        : 'niuu:rounded-md niuu:border niuu:border-brand/60 niuu:text-brand',
                    )}
                  >
                    {mount.lifecycle === 'ephemeral' ? 'E' : 'M'}
                  </div>
                  <div className="niuu:flex niuu:flex-col niuu:leading-tight niuu:min-w-0 niuu:flex-1">
                    <span className="niuu:text-text-primary niuu:font-semibold niuu:truncate niuu:text-[11px]">
                      {mount.name}
                    </span>
                    <span className="niuu:text-[9px] niuu:text-text-faint niuu:font-mono">
                      {mount.lifecycle === 'ephemeral'
                        ? 'workspace-local scratch'
                        : `${mount.role} · ${mount.kind}`}
                      {(mount.categories ?? []).length > 0
                        ? ` · ${(mount.categories ?? []).slice(0, 2).join(', ')}`
                        : ''}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          </>
        )}

        {/* Persona groups */}
        {groups.map(([role, entries]) => (
          <div key={role}>
            <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-2 niuu:mb-1.5 niuu:mt-4 niuu:px-0.5">
              <div className="niuu:flex niuu:flex-col niuu:gap-0.5">
                <span className="niuu:text-[9px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.22em] niuu:text-text-faint niuu:font-mono">
                  ACTORS · {role}
                </span>
              </div>
              <span className="niuu:text-[9px] niuu:text-text-faint niuu:font-mono niuu:mt-0.5">
                {entries.length}
              </span>
            </div>
            <div className="niuu:flex niuu:flex-col niuu:gap-1.5">
              {entries.map((persona) => (
                <button
                  type="button"
                  key={persona.id}
                  data-testid={`persona-chip-${persona.id}`}
                  draggable
                  onClick={() => {
                    onAddPersona?.(persona.id);
                    if (onAddPersona) onClose?.();
                  }}
                  onDragStart={(e) => {
                    e.dataTransfer.setData('application/niuu-persona-id', persona.id);
                    e.dataTransfer.effectAllowed = 'copy';
                  }}
                  className="niuu:w-full niuu:py-2.5 niuu:px-3 niuu:bg-bg-elevated niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:cursor-grab niuu:text-xs niuu:text-text-primary niuu:font-sans niuu:select-none niuu:flex niuu:items-start niuu:gap-2.5 niuu:text-left niuu:hover:border-border"
                >
                  {(() => {
                    const glyph = personaGlyph(persona.role);
                    if (glyph.shape === 'dashed-circle') {
                      return (
                        <div className="niuu:flex niuu:h-7 niuu:w-7 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-dashed niuu:border-brand niuu:text-brand niuu:font-mono niuu:text-[11px]">
                          {glyph.text}
                        </div>
                      );
                    }
                    if (glyph.shape === 'triangle') {
                      return (
                        <div className="niuu:relative niuu:h-7 niuu:w-7">
                          <div className="niuu:absolute niuu:inset-0 niuu:flex niuu:items-center niuu:justify-center niuu:text-brand niuu:font-mono niuu:text-[11px]">
                            △
                          </div>
                          <div className="niuu:absolute niuu:inset-0 niuu:flex niuu:items-center niuu:justify-center niuu:text-brand niuu:font-mono niuu:text-[8px]">
                            {glyph.text}
                          </div>
                        </div>
                      );
                    }
                    return (
                      <div className="niuu:flex niuu:h-7 niuu:w-7 niuu:items-center niuu:justify-center niuu:rounded-md niuu:border niuu:border-brand niuu:text-brand niuu:font-mono niuu:text-[11px]">
                        {glyph.text}
                      </div>
                    );
                  })()}
                  <div className="niuu:flex niuu:flex-col niuu:leading-tight niuu:min-w-0 niuu:flex-1">
                    <span className="niuu:text-text-primary niuu:font-semibold niuu:truncate niuu:text-[11px]">
                      {persona.label}
                    </span>
                    <span className="niuu:text-[9px] niuu:text-text-faint niuu:font-mono">
                      {persona.role}
                    </span>
                  </div>
                  <span className="niuu:ml-auto niuu:text-text-faint niuu:text-[9px] niuu:font-mono niuu:mt-0.5">
                    ⇆
                  </span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
