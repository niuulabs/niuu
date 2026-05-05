/**
 * LintPage — 2-column lint view (matches web2 prototype).
 *
 * LEFT (220px): checks sidebar — All + per-rule rows with name, count, FIX label
 * RIGHT (1fr):  issues list filtered by selected check + Run lint / Auto-fix buttons
 *
 * KPI strip spans the full content width as 4 equal columns with subtitles
 * showing which rules contribute to each count.
 */

import { useState } from 'react';
import { StateDot } from '@niuulabs/ui';
import { useLint } from '../application/useLint';
import type { LintRule, IssueSeverity } from '../domain/lint';

const RULE_DESCRIPTIONS: Record<LintRule, string> = {
  L01: 'Contradiction between pages',
  L02: 'Stale source (page not recompiled)',
  L05: 'Broken wikilink',
  L07: 'Orphan page (no inbound links)',
  L11: 'Stale mount index',
  L12: 'Invalid frontmatter',
};

const ACTION_BTN =
  'niuu-bg-transparent niuu-border niuu-border-solid niuu-border-border-subtle niuu-rounded-sm ' +
  'niuu-text-text-secondary niuu-font-sans niuu-text-xs niuu-py-[2px] niuu-px-2 niuu-cursor-pointer niuu-whitespace-nowrap ' +
  'hover:niuu-border-border hover:niuu-text-text-primary disabled:niuu-opacity-50 disabled:niuu-cursor-not-allowed';
const BTN_PRIMARY =
  'niuu-py-1 niuu-px-3 niuu-rounded-md niuu-font-sans niuu-text-xs niuu-cursor-pointer niuu-whitespace-nowrap ' +
  'niuu-border niuu-bg-brand niuu-border-brand niuu-text-bg-primary niuu-font-medium hover:niuu-opacity-[0.88] ' +
  'disabled:niuu-opacity-50 disabled:niuu-cursor-not-allowed';

const CHECK_ROW_BASE =
  'niuu-grid niuu-grid-cols-[1fr_28px] niuu-items-center niuu-gap-2 niuu-py-2 niuu-px-3 ' +
  'niuu-border-0 niuu-border-b niuu-border-solid niuu-border-border-subtle ' +
  'niuu-cursor-pointer niuu-text-left niuu-w-full niuu-transition-colors';

export function LintPage() {
  const { issues, summary, isLoading, isError, error, runAutoFix, isFixing } = useLint();
  const [selectedRule, setSelectedRule] = useState<LintRule | null>(null);

  const { countByRule, autoFixByRule } = issues.reduce<{
    countByRule: Record<string, number>;
    autoFixByRule: Record<string, boolean>;
  }>(
    (acc, issue) => {
      acc.countByRule[issue.rule] = (acc.countByRule[issue.rule] ?? 0) + 1;
      if (issue.autoFix) acc.autoFixByRule[issue.rule] = true;
      return acc;
    },
    { countByRule: {}, autoFixByRule: {} },
  );

  const rules = Object.keys(RULE_DESCRIPTIONS) as LintRule[];
  const filtered = selectedRule ? issues.filter((i) => i.rule === selectedRule) : issues;
  const totalAutoFixable = issues.filter((i) => i.autoFix).length;
  const autoFixableIds = filtered.filter((i) => i.autoFix).map((i) => i.id);
  const totalLint = summary.error + summary.warn + summary.info;

  // Build subtitle strings showing which rules contribute to each KPI
  const rulesByseverity = (sev: IssueSeverity) =>
    [...new Set(issues.filter((i) => i.severity === sev).map((i) => i.rule))].sort().join(' · ');
  const autoFixRules = [...new Set(issues.filter((i) => i.autoFix).map((i) => i.rule))]
    .sort()
    .join(' · ');

  const KPI_LBL = 'niuu-text-[10px] niuu-uppercase niuu-tracking-[0.07em] niuu-text-text-muted';
  const KPI_VAL = 'niuu-text-2xl niuu-font-medium niuu-font-mono niuu-tracking-[-0.01em]';
  const KPI_SUB =
    'niuu-text-[10px] niuu-text-text-muted niuu-font-mono niuu-whitespace-nowrap niuu-overflow-hidden niuu-text-ellipsis';

  if (isLoading) {
    return (
      <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-text-text-secondary niuu-text-sm niuu-p-6">
        <StateDot state="processing" pulse />
        <span>loading lint report…</span>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-text-text-secondary niuu-text-sm niuu-p-6">
        <StateDot state="failed" />
        <span>{error instanceof Error ? error.message : 'lint load failed'}</span>
      </div>
    );
  }

  return (
    <div className="niuu-p-6 niuu-flex niuu-flex-col niuu-gap-4 niuu-h-full niuu-box-border">
      {/* ── KPI strip — full-width 4-column grid ────────────────── */}
      <div className="niuu-grid niuu-grid-cols-4 niuu-border niuu-border-border-subtle niuu-rounded-md niuu-overflow-hidden">
        <div className="niuu-flex niuu-flex-col niuu-gap-[2px] niuu-py-3 niuu-px-4 niuu-bg-bg-secondary niuu-border-r niuu-border-border-subtle">
          <span className={KPI_LBL}>total issues</span>
          <span className={`${KPI_VAL} niuu-text-brand-400`}>{totalLint}</span>
          <span className={KPI_SUB}>across all mounts</span>
        </div>
        <div className="niuu-flex niuu-flex-col niuu-gap-[2px] niuu-py-3 niuu-px-4 niuu-bg-bg-secondary niuu-border-r niuu-border-border-subtle">
          <span className={KPI_LBL}>errors</span>
          <span
            className={`${KPI_VAL} ${summary.error > 0 ? 'niuu-text-critical-fg' : 'niuu-text-text-muted'}`}
          >
            {summary.error}
          </span>
          <span className={KPI_SUB}>{rulesByseverity('error') || '—'}</span>
        </div>
        <div className="niuu-flex niuu-flex-col niuu-gap-[2px] niuu-py-3 niuu-px-4 niuu-bg-bg-secondary niuu-border-r niuu-border-border-subtle">
          <span className={KPI_LBL}>warnings</span>
          <span className={`${KPI_VAL} niuu-text-brand-400`}>{summary.warn}</span>
          <span className={KPI_SUB}>{rulesByseverity('warn') || '—'}</span>
        </div>
        <div className="niuu-flex niuu-flex-col niuu-gap-[2px] niuu-py-3 niuu-px-4 niuu-bg-bg-secondary">
          <span className={KPI_LBL}>auto-fixable</span>
          <span className={`${KPI_VAL} niuu-text-brand-300`}>{totalAutoFixable}</span>
          <span className={KPI_SUB}>{autoFixRules || '—'}</span>
        </div>
      </div>

      {/* ── 2-column layout ─────────────────────────────────────── */}
      <div className="niuu-grid niuu-grid-cols-[220px_1fr] niuu-border niuu-border-border-subtle niuu-rounded-lg niuu-overflow-hidden niuu-flex-1 niuu-min-h-0">
        {/* LEFT: checks sidebar */}
        <aside
          className="niuu-border-r niuu-border-border-subtle niuu-bg-bg-secondary niuu-flex niuu-flex-col niuu-overflow-y-auto"
          aria-label="Lint checks"
        >
          <h4 className="niuu-py-3 niuu-px-4 niuu-m-0 niuu-text-xs niuu-uppercase niuu-tracking-[0.07em] niuu-text-text-muted niuu-border-b niuu-border-border-subtle niuu-shrink-0">
            Checks
          </h4>
          <button
            type="button"
            className={`${CHECK_ROW_BASE} ${selectedRule === null ? 'niuu-bg-bg-elevated' : 'niuu-bg-transparent hover:niuu-bg-bg-tertiary'}`}
            onClick={() => setSelectedRule(null)}
            aria-pressed={selectedRule === null}
          >
            <div>
              <span className="niuu-font-mono niuu-text-xs niuu-text-text-primary niuu-font-semibold">
                All
              </span>
              <span className="niuu-text-xs niuu-text-text-secondary niuu-ml-2">every issue</span>
            </div>
            <span className="niuu-font-mono niuu-text-xs niuu-text-text-primary niuu-text-right">
              {totalLint}
            </span>
          </button>
          {rules.map((rule) => {
            const count = countByRule[rule] ?? 0;
            const canFix = autoFixByRule[rule];
            return (
              <button
                key={rule}
                type="button"
                className={`${CHECK_ROW_BASE} ${selectedRule === rule ? 'niuu-bg-bg-elevated' : 'niuu-bg-transparent hover:niuu-bg-bg-tertiary'}`}
                onClick={() => setSelectedRule(selectedRule === rule ? null : rule)}
                aria-pressed={selectedRule === rule}
                data-testid="check-row"
              >
                <div className="niuu-flex niuu-flex-col niuu-gap-px">
                  <div className="niuu-flex niuu-items-center niuu-gap-1">
                    <span className="niuu-font-mono niuu-text-xs niuu-text-text-primary niuu-font-semibold">
                      {rule}
                    </span>
                    <span className="niuu-text-xs niuu-text-text-secondary niuu-whitespace-nowrap niuu-overflow-hidden niuu-text-ellipsis">
                      {RULE_DESCRIPTIONS[rule]}
                    </span>
                  </div>
                  {canFix && (
                    <span className="niuu-text-[10px] niuu-font-mono niuu-text-text-muted niuu-uppercase">
                      fix
                    </span>
                  )}
                </div>
                <span
                  className={`niuu-font-mono niuu-text-xs niuu-text-right ${
                    count === 0 ? 'niuu-text-text-muted' : 'niuu-text-text-primary'
                  }`}
                >
                  {count}
                </span>
              </button>
            );
          })}
        </aside>

        {/* RIGHT: issues panel */}
        <div className="niuu-flex niuu-flex-col niuu-overflow-hidden niuu-bg-bg-primary">
          {/* Issues header */}
          <div className="niuu-flex niuu-items-center niuu-justify-between niuu-py-3 niuu-px-4 niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary niuu-shrink-0 niuu-flex-wrap niuu-gap-2">
            <div className="niuu-flex niuu-items-center niuu-gap-2">
              <span className="niuu-text-sm niuu-font-medium niuu-text-text-primary">
                {selectedRule
                  ? `${selectedRule} — ${RULE_DESCRIPTIONS[selectedRule]}`
                  : 'All lint issues'}
              </span>
              <span className="niuu-text-xs niuu-font-mono niuu-text-text-muted">
                · {filtered.length}
              </span>
            </div>
            <div className="niuu-flex niuu-gap-2">
              <button type="button" className={ACTION_BTN} aria-label="Run lint">
                Run lint
              </button>
              {autoFixableIds.length > 0 && (
                <button
                  type="button"
                  className={BTN_PRIMARY}
                  onClick={() => runAutoFix()}
                  disabled={isFixing}
                  aria-label="Fix all auto-fixable issues"
                  data-testid="fix-all-btn"
                >
                  {isFixing ? 'fixing…' : `Auto-fix (${autoFixableIds.length})`}
                </button>
              )}
            </div>
          </div>

          {filtered.length === 0 && (
            <p className="niuu-p-4 niuu-text-text-muted niuu-text-sm niuu-m-0">
              {selectedRule ? `No issues for ${selectedRule}.` : 'No issues found. ✓'}
            </p>
          )}

          <ul
            className="niuu-list-none niuu-p-0 niuu-m-0 niuu-overflow-y-auto niuu-flex-1"
            aria-label="Lint issues"
          >
            {filtered.map((issue, i) => (
              <li
                key={`${issue.rule}-${i}`}
                className="niuu-flex niuu-items-start niuu-gap-3 niuu-py-3 niuu-px-4 niuu-border-b niuu-border-b-border-subtle last:niuu-border-b-0"
                data-testid="lint-issue"
              >
                <code className="niuu-font-mono niuu-text-xs niuu-bg-bg-tertiary niuu-px-2 niuu-py-[2px] niuu-rounded-sm niuu-text-text-secondary niuu-shrink-0 niuu-mt-[2px]">
                  {issue.rule}
                </code>
                <div className="niuu-min-w-0 niuu-flex-1">
                  <div className="niuu-flex niuu-items-center niuu-gap-1">
                    <span className="niuu-font-mono niuu-text-xs niuu-text-text-primary niuu-whitespace-nowrap niuu-overflow-hidden niuu-text-ellipsis">
                      {issue.page}
                    </span>
                    <span className="niuu-text-[10px] niuu-text-text-muted">·</span>
                    <span className="niuu-text-[10px] niuu-text-text-muted niuu-whitespace-nowrap">
                      {issue.mount}
                    </span>
                  </div>
                  <div className="niuu-text-xs niuu-text-text-secondary niuu-mt-0.5">
                    {issue.message}
                  </div>
                </div>
                <div className="niuu-flex niuu-items-center niuu-gap-1 niuu-shrink-0">
                  {issue.autoFix && (
                    <button
                      type="button"
                      className={ACTION_BTN}
                      onClick={() => runAutoFix([issue.id])}
                      disabled={isFixing}
                      aria-label={`Auto-fix issue ${issue.id}`}
                      data-testid="autofix-btn"
                    >
                      {isFixing ? '…' : 'Fix'}
                    </button>
                  )}
                  <button type="button" className={ACTION_BTN} aria-label={`Open ${issue.page}`}>
                    Open
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
