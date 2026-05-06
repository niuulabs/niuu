import { useState } from 'react';
import { Dialog, DialogContent } from '../Dialog';
import { cn } from '../../utils/cn';
import type { Tool, ToolRegistry, ToolGroup } from '@niuulabs/domain';
import './ToolPicker.css';

const GROUP_LABELS: Record<ToolGroup, string> = {
  fs: 'File System',
  shell: 'Shell',
  git: 'Git',
  mimir: 'Mímir',
  observe: 'Observe',
  security: 'Security',
  bus: 'Event Bus',
};

export interface ToolPickerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Full tool registry to display. */
  registry: ToolRegistry;
  /** Currently selected tool ids. */
  selected: string[];
  /** Tool ids that should be excluded (already in the opposing list). */
  excluded?: string[];
  onToggle: (toolId: string) => void;
  /** Label shown in dialog title: "Allow list" | "Deny list" */
  label?: string;
}

/**
 * ToolPicker — Dialog with tool list grouped by provider group.
 * Destructive tools are flagged with a red indicator.
 *
 * Promoted to @niuulabs/ui because Tyr's WorkflowBuilder will need it too.
 * Built here in NIU-673 (Ravn personas page).
 */
export function ToolPicker({
  open,
  onOpenChange,
  registry,
  selected,
  excluded = [],
  onToggle,
  label = 'Select tools',
}: ToolPickerProps) {
  const [search, setSearch] = useState('');

  const q = search.toLowerCase();
  const filtered = registry.filter(
    (t) =>
      !excluded.includes(t.id) &&
      (t.id.toLowerCase().includes(q) || t.desc.toLowerCase().includes(q)),
  );

  const byGroup = filtered.reduce<Record<string, Tool[]>>((acc, tool) => {
    const g = tool.group;
    if (!acc[g]) acc[g] = [];
    acc[g]!.push(tool);
    return acc;
  }, {});

  const groups = Object.keys(byGroup) as ToolGroup[];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={label} className="niuu-tool-picker">
        <input
          type="search"
          placeholder="Search tools…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="niuu-form-control niuu-tool-picker__search"
          aria-label="Search tools"
        />
        <div className="niuu-tool-picker__groups" role="listbox" aria-multiselectable="true">
          {groups.map((group) => (
            <div key={group} className="niuu-tool-picker__group">
              <div className="niuu-tool-picker__group-label">{GROUP_LABELS[group] ?? group}</div>
              {byGroup[group]!.map((tool) => {
                const isSelected = selected.includes(tool.id);
                return (
                  <button
                    key={tool.id}
                    role="option"
                    aria-selected={isSelected}
                    onClick={() => onToggle(tool.id)}
                    className={cn(
                      'niuu-tool-picker__item',
                      isSelected && 'niuu-tool-picker__item--selected',
                      tool.destructive && 'niuu-tool-picker__item--destructive',
                    )}
                    title={tool.desc}
                  >
                    {tool.destructive && (
                      <span
                        className="niuu-tool-picker__destructive-dot"
                        aria-label="destructive"
                      />
                    )}
                    <span className="niuu-tool-picker__item-id">{tool.id}</span>
                    <span className="niuu-tool-picker__item-desc">{tool.desc}</span>
                    {isSelected && (
                      <span className="niuu-tool-picker__check" aria-hidden>
                        ✓
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          ))}
          {groups.length === 0 && (
            <p className="niuu-tool-picker__empty">No tools match your search.</p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
