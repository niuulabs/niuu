/**
 * ValidationPanel — bottom bar showing validation badges and zoom controls.
 *
 * Matches web2 layout: left side has ERR/WARN/REVIEW badges, center has
 * help text, right side has zoom controls (+, -, %, reset, 1:1).
 *
 * Owner: plugin-tyr (WorkflowBuilder).
 */

import { useState, useMemo } from 'react';
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
  no_producer: '←',
  no_consumer: '→',
  resource_link: '⛁',
};

const ZOOM_BTN =
  'niuu-bg-bg-elevated niuu-border niuu-border-border niuu-text-text-secondary niuu-rounded-md niuu-px-2.5 niuu-py-1 niuu-text-xs niuu-cursor-pointer niuu-font-mono hover:niuu-text-text-primary niuu-transition-colors';

export function ValidationPanel({
  workflow,
  onSelectNode,
  errorCount,
  warnCount,
}: ValidationPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const issues = useMemo(() => validateWorkflowFull(workflow), [workflow]);
  const reviewCount = issues.filter((i) => i.kind === 'missing_persona').length;

  return (
    <div
      data-testid="validation-panel"
      className="niuu-absolute niuu-bottom-0 niuu-left-0 niuu-right-0 niuu-z-20 niuu-flex niuu-flex-col niuu-items-center"
    >
      {/* Expanded issue list */}
      {expanded && issues.length > 0 && (
        <div className="niuu-bg-bg-secondary niuu-border niuu-border-border niuu-rounded-md niuu-py-1.5 niuu-px-1 niuu-min-w-[280px] niuu-max-w-[400px] niuu-max-h-[240px] niuu-overflow-y-auto niuu-shadow-md niuu-mb-1">
          {issues.map((issue, idx) => (
            <button
              key={idx}
              data-testid={`validation-issue-${issue.nodeId ?? 'global'}`}
              data-kind={issue.kind}
              onClick={() => {
                if (issue.nodeId) onSelectNode(issue.nodeId);
              }}
              className={cn(
                'niuu-flex niuu-items-start niuu-gap-2 niuu-w-full niuu-px-2.5 niuu-py-1.5 niuu-bg-transparent niuu-border-none niuu-rounded niuu-text-left niuu-font-sans',
                issue.nodeId
                  ? 'niuu-cursor-pointer hover:niuu-bg-bg-elevated'
                  : 'niuu-cursor-default',
              )}
            >
              <span
                className={cn(
                  'niuu-shrink-0 niuu-text-sm niuu-leading-snug niuu-w-[18px] niuu-text-center',
                  issue.severity === 'error' ? 'niuu-text-critical' : 'niuu-text-status-amber',
                )}
              >
                {KIND_ICON[issue.kind]}
              </span>
              <span className="niuu-text-xs niuu-text-text-secondary niuu-leading-snug">
                {issue.message}
              </span>
            </button>
          ))}
        </div>
      )}

      {/* Bottom bar */}
      <div className="niuu-w-full niuu-flex niuu-items-center niuu-justify-between niuu-bg-bg-secondary/95 niuu-backdrop-blur-sm niuu-border-t niuu-border-border niuu-px-3 niuu-py-2">
        {/* Left: validation badges */}
        <button
          data-testid="validation-pill"
          data-issue-count={issues.length}
          onClick={() => setExpanded((e) => !e)}
          className="niuu-bg-transparent niuu-border-none niuu-p-0 niuu-cursor-pointer niuu-flex niuu-items-center niuu-gap-1.5"
        >
          <span
            className={cn(
              'niuu-w-2 niuu-h-2 niuu-rounded-full niuu-shrink-0',
              errorCount > 0
                ? 'niuu-bg-critical'
                : warnCount > 0
                  ? 'niuu-bg-status-amber'
                  : 'niuu-bg-status-emerald',
            )}
          />
          {errorCount > 0 && (
            <span className="niuu-inline-flex niuu-items-center niuu-gap-0.5 niuu-rounded niuu-border niuu-border-critical niuu-bg-critical-bg niuu-px-1.5 niuu-py-0.5 niuu-text-[10px] niuu-font-mono niuu-font-semibold niuu-text-critical">
              ERR {errorCount}
            </span>
          )}
          {warnCount > 0 && (
            <span className="niuu-inline-flex niuu-items-center niuu-gap-0.5 niuu-rounded niuu-border niuu-border-status-amber niuu-bg-status-amber/10 niuu-px-1.5 niuu-py-0.5 niuu-text-[10px] niuu-font-mono niuu-font-semibold niuu-text-status-amber">
              WARN {warnCount}
            </span>
          )}
          {reviewCount > 0 && (
            <span className="niuu-inline-flex niuu-items-center niuu-gap-0.5 niuu-rounded niuu-border niuu-border-border niuu-bg-bg-elevated niuu-px-1.5 niuu-py-0.5 niuu-text-[10px] niuu-font-mono niuu-font-semibold niuu-text-text-secondary">
              REVIEW {reviewCount}
            </span>
          )}
          {issues.length > 0 && (
            <span className="niuu-opacity-60 niuu-text-[10px] niuu-text-text-muted">
              {expanded ? '▲' : '▼'}
            </span>
          )}
        </button>

        {/* Center: help text */}
        <span className="niuu-text-[10px] niuu-text-text-faint niuu-font-mono niuu-uppercase niuu-tracking-wide">
          ⌘/ctrl + scroll to zoom · drag bg to pan
        </span>

        {/* Right: zoom controls */}
        <div className="niuu-flex niuu-items-center niuu-gap-1">
          <button type="button" className={ZOOM_BTN}>
            +
          </button>
          <button type="button" className={ZOOM_BTN}>
            −
          </button>
          <span className="niuu-text-[10px] niuu-font-mono niuu-text-text-secondary niuu-px-1">
            36%
          </span>
          <button type="button" className={ZOOM_BTN} title="Reset zoom">
            ⟲
          </button>
          <button type="button" className={ZOOM_BTN} title="Fit to view">
            1:1
          </button>
        </div>
      </div>
    </div>
  );
}
