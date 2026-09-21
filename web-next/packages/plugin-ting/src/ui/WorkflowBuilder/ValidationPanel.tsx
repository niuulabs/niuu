/** Compact workflow issue drawer anchored over the canvas. */

import { useMemo, useState } from 'react';
import { cn } from '@niuulabs/ui';
import type { Workflow } from '../../domain/workflow';
import type { WorkflowIssue } from '../../domain/workflowValidation';
import { validateWorkflowFull } from '../../domain/workflowValidation';

export interface ValidationPanelProps {
  workflow: Workflow;
  onSelectNode: (id: string) => void;
  errorCount: number;
  warnCount: number;
}

const KIND_ICON: Record<WorkflowIssue['kind'], string> = {
  cycle: '↻',
  orphan: '○',
  dangling_condition: '⚡',
  confidence_underset: '?',
  missing_persona: '👤',
  missing_model: '◇',
  persona_model_conflict: '⇄',
  no_producer: '←',
  no_consumer: '→',
  resource_link: '⛁',
  subworkflow_dependency: '⌁',
  subworkflow_template: '❖',
  subworkflow_coordinator: '♙',
  subworkflow_blocked_event: 'Ⅱ',
  subworkflow_joined_event: '▶',
  subworkflow_limits: '≡',
};

export function ValidationPanel({
  workflow,
  onSelectNode,
  errorCount,
  warnCount,
}: ValidationPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const issues = useMemo(() => validateWorkflowFull(workflow), [workflow]);
  const reviewCount = issues.filter((issue) => issue.kind === 'missing_persona').length;

  return (
    <div
      data-testid="validation-panel"
      className="niuu:pointer-events-none niuu:absolute niuu:bottom-4 niuu:left-40 niuu:z-20 niuu:flex niuu:flex-col niuu:items-start"
    >
      {expanded && issues.length > 0 ? (
        <div className="niuu:pointer-events-auto niuu:mb-2 niuu:max-h-[280px] niuu:min-w-[280px] niuu:max-w-[420px] niuu:overflow-y-auto niuu:rounded-lg niuu:border niuu:border-border niuu:bg-bg-secondary niuu:px-1 niuu:py-1.5 niuu:shadow-2xl">
          {issues.map((issue, index) => (
            <button
              type="button"
              key={`${issue.kind}-${issue.nodeId ?? 'global'}-${index}`}
              data-testid={`validation-issue-${issue.nodeId ?? 'global'}`}
              data-kind={issue.kind}
              onClick={() => {
                if (issue.nodeId) onSelectNode(issue.nodeId);
              }}
              className={cn(
                'niuu:flex niuu:w-full niuu:items-start niuu:gap-2 niuu:rounded niuu:border-none niuu:bg-transparent niuu:px-2.5 niuu:py-2 niuu:text-left niuu:font-sans',
                issue.nodeId
                  ? 'niuu:cursor-pointer niuu:hover:bg-bg-elevated'
                  : 'niuu:cursor-default',
              )}
            >
              <span
                className={cn(
                  'niuu:w-[18px] niuu:shrink-0 niuu:text-center niuu:text-sm niuu:leading-snug',
                  issue.severity === 'error' ? 'niuu:text-critical' : 'niuu:text-status-amber',
                )}
              >
                {KIND_ICON[issue.kind]}
              </span>
              <span className="niuu:text-xs niuu:leading-snug niuu:text-text-secondary">
                {issue.message}
              </span>
            </button>
          ))}
        </div>
      ) : null}

      <button
        type="button"
        data-testid="validation-pill"
        data-issue-count={issues.length}
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
        className="niuu:pointer-events-auto niuu:flex niuu:cursor-pointer niuu:items-center niuu:gap-1.5 niuu:rounded-lg niuu:border niuu:border-border niuu:bg-bg-secondary/95 niuu:px-2.5 niuu:py-2 niuu:shadow-lg niuu:backdrop-blur-sm"
      >
        <span
          className={cn(
            'niuu:h-2 niuu:w-2 niuu:shrink-0 niuu:rounded-full',
            errorCount > 0
              ? 'niuu:bg-critical'
              : warnCount > 0
                ? 'niuu:bg-status-amber'
                : 'niuu:bg-status-emerald',
          )}
        />
        {errorCount > 0 ? (
          <span className="niuu:text-[10px] niuu:font-mono niuu:font-semibold niuu:text-critical">
            ERR {errorCount}
          </span>
        ) : null}
        {warnCount > 0 ? (
          <span className="niuu:text-[10px] niuu:font-mono niuu:font-semibold niuu:text-status-amber">
            WARN {warnCount}
          </span>
        ) : null}
        {reviewCount > 0 ? (
          <span className="niuu:text-[10px] niuu:font-mono niuu:text-text-secondary">
            REVIEW {reviewCount}
          </span>
        ) : null}
        {issues.length === 0 ? (
          <span className="niuu:text-[10px] niuu:font-mono niuu:text-text-secondary">
            No issues
          </span>
        ) : (
          <span className="niuu:text-[10px] niuu:text-text-muted">{expanded ? '▲' : '▼'}</span>
        )}
      </button>
    </div>
  );
}
