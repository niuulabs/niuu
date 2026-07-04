import { useMemo, useState } from 'react';
import { CheckCircle2, ChevronDown, ChevronRight, Moon } from 'lucide-react';
import type { ReviewStatus, ValkyrieEventTelemetry } from '../domain';
import {
  DEFAULT_STORY_FILTERS,
  actionStatus,
  eventSkillName,
  filterActivityStories,
  groupActivityStories,
  type ActivityStory,
  type ActivityStoryFilters,
} from '../application/activityStories';
import { useValkyrieDashboard } from '../application/useValkyrieDashboard';
import { useReviewList } from '../application/useReviews';
import { useValkyrieSkills } from '../application/useValkyrieSkills';
import { ActivityDebugList, eventClasses } from './ActivityDebugList';
import { SkillNameButton, SkillViewer } from './SkillViewer';
import { timeAgo } from './reviewFormat';

const PANEL =
  'niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:rounded-md';
const MUTED = 'niuu:text-text-muted';
const SETTLED: ReviewStatus[] = ['approved', 'rejected', 'applied', 'apply_failed', 'expired'];

function toggleClasses(active: boolean): string {
  return `niuu:rounded-md niuu:border niuu:border-solid niuu:px-2 niuu:py-1 niuu:text-xs ${
    active
      ? 'niuu:border-brand niuu:bg-brand/15 niuu:text-brand'
      : `niuu:border-border niuu:bg-bg-secondary ${MUTED}`
  }`;
}

function actionStatusClasses(status: string): string {
  if (status === 'failed') return 'niuu:bg-critical-bg niuu:text-critical';
  if (status === 'completed' || status === 'succeeded')
    return 'niuu:bg-bg-tertiary niuu:text-state-ok';
  return 'niuu:bg-bg-tertiary niuu:text-state-warn';
}

function ThreadEventRow({
  event,
  skillNames,
  onViewSkill,
}: {
  event: ValkyrieEventTelemetry;
  skillNames: readonly string[];
  onViewSkill: (name: string) => void;
}) {
  const skillName = event.kind === 'judgment' ? eventSkillName(event, skillNames) : '';
  const status = actionStatus(event);
  return (
    <li data-testid="story-event" className="niuu:flex niuu:items-start niuu:gap-2">
      <span className={`niuu:w-16 niuu:shrink-0 niuu:text-xs ${eventClasses(event.kind)}`}>
        {event.kind}
      </span>
      <div className="niuu:min-w-0 niuu:flex-1">
        <p className="niuu:text-sm niuu:text-text-primary">
          {event.summary || event.eventType}
          {status ? (
            <span
              data-testid="story-action-status"
              className={`niuu:ml-2 niuu:rounded-full niuu:px-2 niuu:py-0.5 niuu:text-[11px] ${actionStatusClasses(status)}`}
            >
              {status}
            </span>
          ) : null}
        </p>
        <p className={`niuu:mt-0.5 niuu:text-xs ${MUTED}`}>
          {event.valkyrieName || event.valkyrieId || event.source || event.eventType}
          {skillName ? (
            <>
              {' · '}
              <SkillNameButton name={skillName} onOpen={onViewSkill} />
            </>
          ) : null}
        </p>
      </div>
      <span className={`niuu:shrink-0 niuu:text-xs ${MUTED}`}>{timeAgo(event.observedAt)} ago</span>
    </li>
  );
}

function StoryThread({
  story,
  onViewSkill,
}: {
  story: ActivityStory;
  onViewSkill: (environmentId: string, name: string) => void;
}) {
  const environmentId = story.environmentIds[0] ?? '';
  const skills = useValkyrieSkills(environmentId);
  const skillNames = useMemo(
    () => (skills.data ?? []).map((skill) => skill.skillName),
    [skills.data],
  );
  return (
    <ol
      data-testid="story-thread"
      className="niuu:mt-3 niuu:flex niuu:flex-col niuu:gap-2 niuu:border-t niuu:border-solid niuu:border-border niuu:pt-3"
    >
      {story.events.map((event) => (
        <ThreadEventRow
          key={event.id}
          event={event}
          skillNames={skillNames}
          onViewSkill={(name) => onViewSkill(event.environmentId || environmentId, name)}
        />
      ))}
    </ol>
  );
}

function IdleTriageLine({ story }: { story: ActivityStory }) {
  return (
    <li
      data-testid="story-triage"
      className={`${PANEL} niuu:flex niuu:items-center niuu:justify-between niuu:gap-2 niuu:px-3 niuu:py-2`}
    >
      <div className="niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-2">
        <Moon size={14} className={MUTED} aria-hidden="true" />
        <span className={`niuu:truncate niuu:text-sm ${MUTED}`}>{story.headline}</span>
      </div>
      <span
        className={`niuu:flex niuu:shrink-0 niuu:items-center niuu:gap-2 niuu:text-xs ${MUTED}`}
      >
        {story.environmentIds.join(', ')}
        <span>{timeAgo(story.latestAt)} ago</span>
      </span>
    </li>
  );
}

function InvestigationStory({
  story,
  expanded,
  onToggle,
  onViewSkill,
}: {
  story: ActivityStory;
  expanded: boolean;
  onToggle: () => void;
  onViewSkill: (environmentId: string, name: string) => void;
}) {
  return (
    <li data-testid="story-investigation" className={`${PANEL} niuu:p-3`}>
      <button
        type="button"
        aria-expanded={expanded}
        onClick={onToggle}
        className="niuu:flex niuu:w-full niuu:items-center niuu:justify-between niuu:gap-2 niuu:text-left"
      >
        <span className="niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-2">
          {expanded ? (
            <ChevronDown size={14} className={MUTED} aria-hidden="true" />
          ) : (
            <ChevronRight size={14} className={MUTED} aria-hidden="true" />
          )}
          <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">
            {story.headline}
          </span>
          {story.actionCount > 0 ? (
            <CheckCircle2
              size={14}
              className="niuu:shrink-0 niuu:text-state-ok"
              aria-hidden="true"
            />
          ) : null}
        </span>
        <span
          className={`niuu:flex niuu:shrink-0 niuu:items-center niuu:gap-2 niuu:text-xs ${MUTED}`}
        >
          <span>
            {story.events.length} event{story.events.length === 1 ? '' : 's'}
          </span>
          {story.environmentIds.join(', ')}
          <span>{timeAgo(story.latestAt)} ago</span>
        </span>
      </button>
      {expanded ? <StoryThread story={story} onViewSkill={onViewSkill} /> : null}
    </li>
  );
}

export function ActivityPage() {
  const dashboard = useValkyrieDashboard();
  const reviews = useReviewList();
  const [filters, setFilters] = useState<ActivityStoryFilters>(DEFAULT_STORY_FILTERS);
  const [debugView, setDebugView] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [viewedSkill, setViewedSkill] = useState<{
    environmentId: string;
    skillName: string;
  } | null>(null);

  const telemetryEvents = useMemo(
    () =>
      [...(dashboard.data?.telemetry?.recentEvents ?? [])].sort((a, b) =>
        b.observedAt.localeCompare(a.observedAt),
      ),
    [dashboard.data?.telemetry?.recentEvents],
  );
  const stories = useMemo(() => groupActivityStories(telemetryEvents), [telemetryEvents]);
  const visibleStories = useMemo(() => filterActivityStories(stories, filters), [stories, filters]);
  const settled = useMemo(
    () =>
      (reviews.data ?? [])
        .filter((item) => SETTLED.includes(item.status))
        .sort((a, b) =>
          (b.decidedAt || b.resolvedAt || b.requestedAt).localeCompare(
            a.decidedAt || a.resolvedAt || a.requestedAt,
          ),
        ),
    [reviews.data],
  );

  if (dashboard.isLoading && reviews.isLoading) {
    return (
      <div data-testid="activity-loading" className={`niuu:p-6 niuu:text-sm ${MUTED}`}>
        Loading activity…
      </div>
    );
  }
  const blockingError =
    dashboard.error ??
    (telemetryEvents.length === 0 && settled.length === 0 ? reviews.error : null);
  if (blockingError) {
    return (
      <div
        data-testid="activity-error"
        role="alert"
        className="niuu:m-4 niuu:rounded-md niuu:border niuu:border-solid niuu:border-critical-bo niuu:bg-critical-bg niuu:p-4 niuu:text-sm niuu:text-critical"
      >
        {blockingError instanceof Error ? blockingError.message : 'Unable to load activity'}
      </div>
    );
  }

  const environments = dashboard.data?.environments ?? [];
  const hiddenCount = stories.length - visibleStories.length;

  return (
    <div
      data-testid="activity-page"
      className="niuu:flex niuu:h-full niuu:min-h-0 niuu:flex-col niuu:gap-3 niuu:bg-bg-primary niuu:p-3"
    >
      <header className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2">
        <h1 className="niuu:text-base niuu:text-text-primary">Activity</h1>
        <div className="niuu:ml-auto niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2">
          <select
            aria-label="Filter by environment"
            value={filters.environmentId}
            onChange={(event) =>
              setFilters((current) => ({ ...current, environmentId: event.target.value }))
            }
            className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-primary"
          >
            <option value="">all environments</option>
            {environments.map((environment) => (
              <option key={environment.id} value={environment.id}>
                {environment.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            aria-pressed={filters.showRoutine}
            onClick={() =>
              setFilters((current) => ({ ...current, showRoutine: !current.showRoutine }))
            }
            className={toggleClasses(filters.showRoutine)}
          >
            show routine
          </button>
          <button
            type="button"
            aria-pressed={filters.actionsOnly}
            onClick={() =>
              setFilters((current) => ({ ...current, actionsOnly: !current.actionsOnly }))
            }
            className={toggleClasses(filters.actionsOnly)}
          >
            actions only
          </button>
          <button
            type="button"
            aria-pressed={debugView}
            onClick={() => setDebugView((current) => !current)}
            className={toggleClasses(debugView)}
          >
            debug view
          </button>
          <span className={`niuu:text-xs ${MUTED}`}>
            {dashboard.data?.telemetry?.lastObservedAt
              ? `${timeAgo(dashboard.data.telemetry.lastObservedAt)} ago`
              : ''}
          </span>
        </div>
      </header>
      {telemetryEvents.length === 0 && settled.length === 0 ? (
        <div data-testid="activity-empty" className={`${PANEL} niuu:p-6 niuu:text-sm ${MUTED}`}>
          No telemetry or decisions yet.
        </div>
      ) : debugView ? (
        <ActivityDebugList events={telemetryEvents} settled={settled} />
      ) : visibleStories.length === 0 ? (
        <div
          data-testid="activity-filtered-empty"
          className={`${PANEL} niuu:p-6 niuu:text-sm ${MUTED}`}
        >
          {hiddenCount > 0
            ? `${hiddenCount} stor${hiddenCount === 1 ? 'y is' : 'ies are'} hidden by the current filters.`
            : 'No stories yet — settled decisions live in the debug view.'}
        </div>
      ) : (
        <ol className="niuu:flex niuu:min-h-0 niuu:flex-1 niuu:flex-col niuu:gap-2 niuu:overflow-auto">
          {visibleStories.map((story) =>
            story.kind === 'idle_triage' ? (
              <IdleTriageLine key={story.correlationId} story={story} />
            ) : (
              <InvestigationStory
                key={story.correlationId}
                story={story}
                expanded={expandedId === story.correlationId}
                onToggle={() =>
                  setExpandedId((current) =>
                    current === story.correlationId ? null : story.correlationId,
                  )
                }
                onViewSkill={(environmentId, skillName) =>
                  setViewedSkill({ environmentId, skillName })
                }
              />
            ),
          )}
        </ol>
      )}
      <SkillViewer
        environmentId={viewedSkill?.environmentId ?? ''}
        skillName={viewedSkill?.skillName ?? null}
        onClose={() => setViewedSkill(null)}
      />
    </div>
  );
}
