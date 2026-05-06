import type { LintIssue, LintSeverity, MimirLintReport } from '../../api/types';
import styles from './LintPanel.module.css';

interface LintPanelProps {
  report: MimirLintReport | null;
  onPageClick: (path: string) => void;
}

interface IssueGroupProps {
  title: string;
  issues: LintIssue[];
  variant: LintSeverity;
  onPageClick: (path: string) => void;
}

const CHECK_LABELS: Record<string, string> = {
  L01: 'Orphaned pages',
  L02: 'Contradictions',
  L03: 'Stale sources',
  L04: 'Knowledge gaps',
  L05: 'Broken wikilinks',
  L06: 'Missing source attribution',
  L07: 'Thin pages',
  L08: 'Stale content',
  L09: 'Timeline edits',
  L10: 'Empty compiled truth',
  L11: 'Stale index',
  L12: 'Invalid frontmatter',
};

function IssueGroup({ title, issues, variant, onPageClick }: IssueGroupProps) {
  if (issues.length === 0) {
    return null;
  }

  return (
    <section className={styles.issueGroup}>
      <h3 className={styles.groupTitle} data-variant={variant}>
        <span className={styles.groupCount}>{issues.length}</span>
        {title}
      </h3>
      <ul className={styles.pathList} role="list">
        {issues.map((issue, idx) => (
          <li key={`${issue.id}-${issue.pagePath}-${idx}`} className={styles.pathItem}>
            <button
              className={styles.pathButton}
              onClick={() => onPageClick(issue.pagePath)}
              title={`Open ${issue.pagePath}`}
            >
              <span className={styles.issueId}>{issue.id}</span>
              <span className={styles.issueMessage}>{issue.message}</span>
              {issue.autoFixable && (
                <span className={styles.autoFixBadge} title="Auto-fixable with lint --fix">
                  fixable
                </span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function LintPanel({ report, onPageClick }: LintPanelProps) {
  if (!report) {
    return (
      <div className={styles.empty}>
        <span className={styles.emptyText}>Run a lint check to see the health report</span>
      </div>
    );
  }

  const errors = report.issues.filter(i => i.severity === 'error');
  const warnings = report.issues.filter(i => i.severity === 'warning');
  const infos = report.issues.filter(i => i.severity === 'info');
  const totalIssues = report.issues.length;

  // Group by check ID within each severity tier
  const errorGroups = groupByCheckId(errors);
  const warningGroups = groupByCheckId(warnings);
  const infoGroups = groupByCheckId(infos);

  return (
    <div className={styles.panel}>
      <header className={styles.summary}>
        <div className={styles.summaryStats}>
          <div className={styles.stat}>
            <span className={styles.statValue}>{report.pagesChecked}</span>
            <span className={styles.statLabel}>pages checked</span>
          </div>
          <div className={styles.stat}>
            <span className={styles.statValue} data-has-issues={totalIssues > 0}>
              {totalIssues}
            </span>
            <span className={styles.statLabel}>issues found</span>
          </div>
          {report.summary.error > 0 && (
            <div className={styles.stat}>
              <span className={styles.statValue} data-severity="error">
                {report.summary.error}
              </span>
              <span className={styles.statLabel}>errors</span>
            </div>
          )}
          {report.summary.warning > 0 && (
            <div className={styles.stat}>
              <span className={styles.statValue} data-severity="warning">
                {report.summary.warning}
              </span>
              <span className={styles.statLabel}>warnings</span>
            </div>
          )}
        </div>
        <span className={styles.healthBadge} data-healthy={!report.issuesFound}>
          <span className={styles.healthDot} aria-hidden="true" />
          {report.issuesFound ? 'Issues detected' : 'All clear'}
        </span>
      </header>

      {!report.issuesFound && (
        <div className={styles.allClear}>
          <span className={styles.allClearText}>
            No issues found. Knowledge base looks healthy.
          </span>
        </div>
      )}

      <div className={styles.issueGroups}>
        {errorGroups.map(([checkId, issues]) => (
          <IssueGroup
            key={checkId}
            title={CHECK_LABELS[checkId] ?? checkId}
            issues={issues}
            variant="error"
            onPageClick={onPageClick}
          />
        ))}
        {warningGroups.map(([checkId, issues]) => (
          <IssueGroup
            key={checkId}
            title={CHECK_LABELS[checkId] ?? checkId}
            issues={issues}
            variant="warning"
            onPageClick={onPageClick}
          />
        ))}
        {infoGroups.map(([checkId, issues]) => (
          <IssueGroup
            key={checkId}
            title={CHECK_LABELS[checkId] ?? checkId}
            issues={issues}
            variant="info"
            onPageClick={onPageClick}
          />
        ))}
      </div>
    </div>
  );
}

function groupByCheckId(issues: LintIssue[]): [string, LintIssue[]][] {
  const map = new Map<string, LintIssue[]>();
  for (const issue of issues) {
    const group = map.get(issue.id) ?? [];
    group.push(issue);
    map.set(issue.id, group);
  }
  return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b));
}
