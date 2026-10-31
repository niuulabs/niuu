import type { ReactNode } from 'react';
import {
  LIST_LIMIT,
  type EvolutionChange,
  type EvolutionReport,
  type ValkyrieDashboard,
} from '../domain';
import { retirementReasonCopy } from './copy';
import { timeAgo } from './reviewFormat';
import { SkillNameButton } from './SkillViewer';

const MUTED = 'niuu:text-text-muted';
const ROW =
  'niuu:border-0 niuu:border-t niuu:border-solid niuu:border-border-subtle niuu:py-2 niuu:text-sm';
const TOOL_COLUMNS = 'niuu:grid niuu:grid-cols-[minmax(0,1fr)_72px_64px_84px] niuu:gap-2';
/** Enough to read at a glance; the rest stays one click away. */
const TOP_TOOL_COUNT = 5;
const CHANGE_PREVIEW_COUNT = 5;

function worked(runs: number, successes: number): string {
  return runs > 0 ? `${Math.round((successes / runs) * 100)}%` : '—';
}

function MostUsedTools({
  report,
  onViewSkill,
}: {
  report: EvolutionReport;
  onViewSkill: (name: string) => void;
}) {
  if (report.mostUsed.length === 0) return null;
  return (
    <div data-testid="evolution-most-used" className="niuu:mt-2">
      <div className={`${TOOL_COLUMNS} niuu:pb-1 niuu:text-xs ${MUTED}`}>
        <span>Most used tools</span>
        <span className="niuu:text-right">Runs</span>
        <span className="niuu:text-right">Worked</span>
        <span className="niuu:text-right">Last used</span>
      </div>
      {report.mostUsed.slice(0, TOP_TOOL_COUNT).map((tool) => (
        <div
          key={tool.tool}
          data-testid={`evolution-tool-${tool.tool}`}
          className={`${TOOL_COLUMNS} ${ROW} niuu:text-text-secondary`}
        >
          <span className="niuu:min-w-0 niuu:truncate">
            <SkillNameButton name={tool.tool} onOpen={onViewSkill} />
          </span>
          <span className="niuu:text-right">{tool.runs.toLocaleString()}</span>
          <span className="niuu:text-right">{worked(tool.runs, tool.successes)}</span>
          <span className="niuu:text-right">
            {tool.lastUsedAt ? `${timeAgo(tool.lastUsedAt)} ago` : '—'}
          </span>
        </div>
      ))}
    </div>
  );
}

function changeText(change: EvolutionChange, peerName: string): ReactNode {
  const tool = <span className="niuu:font-mono niuu:text-xs">{change.tool}</span>;
  if (change.kind === 'built') return <>Built {tool}</>;
  if (change.kind === 'repaired') return <>Repaired {tool}</>;
  if (change.kind === 'picked_up') {
    return (
      <>
        Picked up {tool} from {peerName}
      </>
    );
  }
  if (change.replacedBy) {
    return (
      <>
        Retired {tool}, same as{' '}
        <span className="niuu:font-mono niuu:text-xs">{change.replacedBy}</span>
      </>
    );
  }
  return (
    <>
      Retired {tool}. {retirementReasonCopy(change.reason).description}
    </>
  );
}

function ChangeList({
  changes,
  peerName,
}: {
  changes: EvolutionChange[];
  peerName: (id: string) => string;
}) {
  return (
    <ul>
      {changes.map((change) => (
        <li
          key={`${change.kind}:${change.tool}`}
          data-testid={`evolution-change-${change.tool}`}
          className={`niuu:grid niuu:grid-cols-[76px_minmax(0,1fr)] niuu:gap-2 ${ROW}`}
        >
          <span className={MUTED}>{timeAgo(change.at)} ago</span>
          <span className="niuu:text-text-primary">
            {changeText(change, peerName(change.peer))}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** One collapsed layer of detail. */
export function EvolutionFold({
  id,
  label,
  children,
}: {
  id: string;
  label: string;
  children: ReactNode;
}) {
  return (
    <details className="niuu:border-0 niuu:border-t niuu:border-solid niuu:border-border-subtle niuu:py-2">
      <summary
        data-testid={`evolution-fold-${id}`}
        className="niuu:cursor-pointer niuu:text-sm niuu:text-text-secondary"
      >
        {label}
      </summary>
      {children}
    </details>
  );
}

/** What this resident has become good at, and what changed lately. */
export function EvolutionPanel({
  dashboard,
  report,
  onViewSkill,
}: {
  dashboard: ValkyrieDashboard;
  report: EvolutionReport | null;
  onViewSkill: (name: string) => void;
}) {
  if (!report) {
    return (
      <p data-testid="evolution-empty" className={`niuu:text-sm ${MUTED}`}>
        No report yet. It arrives with this resident&apos;s next dream.
      </p>
    );
  }
  const peerName = (id: string) => dashboard.valkyries.find((entry) => entry.id === id)?.name ?? id;
  const older = report.changes.slice(CHANGE_PREVIEW_COUNT, LIST_LIMIT);
  return (
    <section data-testid="valkyrie-evolution">
      <h2
        data-testid="evolution-headline"
        className="niuu:text-sm niuu:font-semibold niuu:text-text-primary"
      >
        {report.headline}
      </h2>
      <MostUsedTools report={report} onViewSkill={onViewSkill} />
      {report.changes.length > 0 ? (
        <div className="niuu:mt-5">
          <h3 className="niuu:pb-1 niuu:text-sm niuu:font-semibold niuu:text-text-primary">
            What changed
          </h3>
          <ChangeList changes={report.changes.slice(0, CHANGE_PREVIEW_COUNT)} peerName={peerName} />
          {older.length > 0 ? (
            <EvolutionFold id="changes" label={`${older.length} older changes`}>
              <ChangeList changes={older} peerName={peerName} />
            </EvolutionFold>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
