import type { ReactNode } from 'react';
import { AlertTriangle, GitMerge, Sparkles, Sprout, Users, Wrench } from 'lucide-react';
import {
  LIST_LIMIT,
  type EvolutionChange,
  type EvolutionReport,
  type ValkyrieDashboard,
  type ValkyrieResident,
} from '../domain';
import { dreamStatusCopy, retirementReasonCopy } from './copy';
import { timeAgo } from './reviewFormat';
import { SkillNameButton } from './SkillViewer';

const MUTED = 'niuu:text-text-muted';

function worked(runs: number, successes: number): string {
  return runs > 0 ? `${Math.round((successes / runs) * 100)}%` : '—';
}

function Tile({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="niuu:rounded-md niuu:bg-bg-primary niuu:p-3">
      <div className={`niuu:text-xs ${MUTED}`}>{label}</div>
      <div className="niuu:mt-1 niuu:text-xl niuu:font-semibold niuu:text-text-primary">
        {value}
      </div>
      <div className={`niuu:text-xs ${MUTED}`}>{detail}</div>
    </div>
  );
}

const CHANGE_ICON: Record<EvolutionChange['kind'], ReactNode> = {
  built: <Sparkles size={15} aria-hidden="true" />,
  repaired: <Wrench size={15} aria-hidden="true" />,
  picked_up: <Users size={15} aria-hidden="true" />,
  retired: <GitMerge size={15} aria-hidden="true" />,
};

const CHANGE_VERB: Record<EvolutionChange['kind'], string> = {
  built: 'Built',
  repaired: 'Repaired',
  picked_up: 'Picked up',
  retired: 'Retired',
};

function changeDetail(change: EvolutionChange): string {
  if (change.kind === 'retired') {
    if (change.replacedBy) return `Replaced by ${change.replacedBy}.`;
    return retirementReasonCopy(change.reason).description;
  }
  if (change.runs === 0) return 'Not used yet.';
  return `${change.successes} of ${change.runs} runs worked.`;
}

function ChangeRow({
  change,
  peerName,
  onViewSkill,
}: {
  change: EvolutionChange;
  peerName: string;
  onViewSkill: (name: string) => void;
}) {
  return (
    <li
      data-testid={`evolution-change-${change.tool}`}
      className="niuu:grid niuu:grid-cols-[52px_18px_minmax(0,1fr)] niuu:gap-2 niuu:border-0 niuu:border-t niuu:border-solid niuu:border-border-subtle niuu:py-2"
    >
      <span className={`niuu:text-xs ${MUTED}`}>{timeAgo(change.at)} ago</span>
      <span className="niuu:text-text-secondary">{CHANGE_ICON[change.kind]}</span>
      <div className="niuu:min-w-0">
        <div className="niuu:truncate niuu:text-sm niuu:text-text-primary">
          {CHANGE_VERB[change.kind]}{' '}
          {change.kind === 'retired' ? (
            <span className="niuu:font-mono niuu:text-xs">{change.tool}</span>
          ) : (
            <SkillNameButton name={change.tool} onOpen={onViewSkill} />
          )}
          {change.kind === 'picked_up' ? ` from ${peerName}` : null}
        </div>
        <div className={`niuu:text-xs ${MUTED}`}>{changeDetail(change)}</div>
      </div>
    </li>
  );
}

function FlockTable({
  dashboard,
  valkyrie,
  onSelect,
}: {
  dashboard: ValkyrieDashboard;
  valkyrie: ValkyrieResident;
  onSelect: (valkyrieId: string) => void;
}) {
  const flock = dashboard.flocks.find(
    (entry) => entry.id === valkyrie.flockId || entry.valkyrieIds.includes(valkyrie.id),
  );
  const members = dashboard.valkyries
    .filter((entry) => flock?.valkyrieIds.includes(entry.id) || entry.id === valkyrie.id)
    .flatMap((entry) => {
      const report = entry.lastDream?.evolution;
      return report ? [{ entry, report }] : [];
    })
    .sort((a, b) => b.report.runs - a.report.runs);
  if (members.length < 2) return null;
  return (
    <div data-testid="evolution-flock">
      <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">The flock</h3>
      <div
        className={`niuu:mt-2 niuu:grid niuu:grid-cols-[minmax(0,1fr)_56px_76px_60px] niuu:gap-2 niuu:text-xs ${MUTED}`}
      >
        <span>Resident</span>
        <span className="niuu:text-right">Tools</span>
        <span className="niuu:text-right">Runs</span>
        <span className="niuu:text-right">Worked</span>
      </div>
      {members.map(({ entry, report }) => {
        const failing = report.attention.length > 0;
        return (
          <button
            key={entry.id}
            type="button"
            data-testid={`evolution-flock-${entry.id}`}
            aria-current={entry.id === valkyrie.id}
            onClick={() => onSelect(entry.id)}
            className={`niuu:grid niuu:w-full niuu:grid-cols-[minmax(0,1fr)_56px_76px_60px] niuu:gap-2 niuu:border-0 niuu:border-t niuu:border-solid niuu:border-border-subtle niuu:bg-transparent niuu:py-1.5 niuu:text-left niuu:text-sm ${
              failing ? 'niuu:text-critical' : 'niuu:text-text-secondary'
            } ${entry.id === valkyrie.id ? 'niuu:font-semibold' : ''}`}
          >
            <span className="niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-1">
              {failing ? <AlertTriangle size={13} aria-label="Has a failing tool" /> : null}
              <span className="niuu:truncate">{entry.name}</span>
            </span>
            <span className="niuu:text-right">{report.tools}</span>
            <span className="niuu:text-right">{report.runs.toLocaleString()}</span>
            <span className="niuu:text-right">{worked(report.runs, report.successes)}</span>
          </button>
        );
      })}
    </div>
  );
}

function Report({
  report,
  dashboard,
  onViewSkill,
}: {
  report: EvolutionReport;
  dashboard: ValkyrieDashboard;
  onViewSkill: (name: string) => void;
}) {
  const peerName = (id: string) => dashboard.valkyries.find((entry) => entry.id === id)?.name ?? id;
  return (
    <>
      <p
        data-testid="evolution-headline"
        className="niuu:mt-3 niuu:text-base niuu:font-medium niuu:text-text-primary"
      >
        {report.headline}
      </p>
      <div className="niuu:mt-3 niuu:grid niuu:gap-3 niuu:md:grid-cols-3">
        <Tile
          label="Tools it keeps"
          value={String(report.tools)}
          detail={`${report.retired} retired so far`}
        />
        <Tile
          label="Runs that worked"
          value={worked(report.runs, report.successes)}
          detail={`of ${report.runs.toLocaleString()} runs`}
        />
        <Tile
          label="From other residents"
          value={String(report.fromPeers)}
          detail={`of its ${report.tools} tools`}
        />
      </div>
      {report.attention.length > 0 ? (
        <ul
          data-testid="evolution-attention"
          className="niuu:mt-3 niuu:flex niuu:flex-col niuu:gap-1"
        >
          {report.attention.map((item) => (
            <li
              key={item.tool}
              className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-sm niuu:text-critical"
            >
              <AlertTriangle size={14} aria-hidden="true" />
              <span>
                <span className="niuu:font-mono niuu:text-xs">{item.tool}</span> failed all{' '}
                {item.runs} runs and is still installed.
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {report.changes.length > 0 ? (
        <div className="niuu:mt-4">
          <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">What changed</h3>
          <ul className="niuu:mt-2">
            {report.changes.slice(0, LIST_LIMIT).map((change) => (
              <ChangeRow
                key={`${change.kind}:${change.tool}`}
                change={change}
                peerName={peerName(change.peer)}
                onViewSkill={onViewSkill}
              />
            ))}
          </ul>
        </div>
      ) : null}
    </>
  );
}

/** One collapsed layer of detail under the report. */
export function EvolutionFold({
  id,
  label,
  defaultOpen = false,
  children,
}: {
  id: string;
  label: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  return (
    <details
      open={defaultOpen}
      className="niuu:border-0 niuu:border-t niuu:border-solid niuu:border-border-subtle niuu:py-2"
    >
      <summary
        data-testid={`evolution-fold-${id}`}
        className="niuu:cursor-pointer niuu:text-sm niuu:font-medium niuu:text-text-secondary"
      >
        {label}
      </summary>
      {children}
    </details>
  );
}

/**
 * How a resident has changed: the report its last dream produced, the flock
 * beside it, and the detail behind both folded underneath.
 */
export function EvolutionPanel({
  dashboard,
  valkyrie,
  onViewSkill,
  onSelectResident,
  children,
}: {
  dashboard: ValkyrieDashboard;
  valkyrie: ValkyrieResident;
  onViewSkill: (name: string) => void;
  onSelectResident: (valkyrieId: string) => void;
  children: ReactNode;
}) {
  const dream = valkyrie.lastDream;
  const report = dream?.evolution ?? null;
  const failed = dream?.status === 'failed';
  return (
    <section
      data-testid="valkyrie-evolution"
      className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:p-4"
    >
      <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <Sprout size={15} className="niuu:text-brand" aria-hidden="true" />
          <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">Evolution</h2>
        </div>
        {dream ? (
          <span className={`niuu:text-xs ${failed ? 'niuu:text-critical' : MUTED}`}>
            last dream {dreamStatusCopy(dream.status).label.toLowerCase()},{' '}
            {timeAgo(dream.finishedAt)} ago
          </span>
        ) : null}
      </div>
      {failed ? (
        <p
          role="alert"
          data-testid="evolution-dream-failed"
          className="niuu:mt-3 niuu:rounded-md niuu:border niuu:border-solid niuu:border-critical-bo niuu:bg-critical-bg niuu:p-2 niuu:font-mono niuu:text-xs niuu:text-critical"
        >
          {dream?.error || 'The dream failed without reporting an error.'}
        </p>
      ) : null}
      {report ? (
        <Report report={report} dashboard={dashboard} onViewSkill={onViewSkill} />
      ) : (
        <p data-testid="evolution-empty" className={`niuu:mt-3 niuu:text-sm ${MUTED}`}>
          No report yet. It arrives with this resident&apos;s next dream.
        </p>
      )}
      <div className="niuu:mt-4 niuu:flex niuu:flex-col niuu:gap-4">
        <FlockTable dashboard={dashboard} valkyrie={valkyrie} onSelect={onSelectResident} />
        <div>{children}</div>
      </div>
    </section>
  );
}
