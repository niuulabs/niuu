/**
 * ReplayTimeline — the scrubber along the bottom of the heads-up display.
 * Always present: at rest it reads "AS OF <today>"; dragging it back
 * replays how memory looked on an earlier day. A thin per-day bar of pages
 * first seen sits over the track, and the focused item's own dated history
 * (first seen, each link introduced) runs beneath it as marks to jump to.
 */
import { formatDay, type ReplayMark } from '../../domain/hud';
import { REPLAY_SPEEDS, type DayCount, type ReplaySpeed } from '../../domain/replayHistogram';
import './MemoryHud.css';

export interface ReplayTimelineProps {
  /** Every day from the earliest page to `today`, inclusive. */
  histogram: DayCount[];
  /** The day shown (ISO date), or null for now. */
  asOf: string | null;
  /** Today's ISO date: the end of the track. */
  today: string;
  /** A day to show, or null to go back to now. */
  onAsOfChange: (date: string | null) => void;
  isPlaying: boolean;
  onTogglePlay: () => void;
  speed: ReplaySpeed;
  onSpeedChange: (speed: ReplaySpeed) => void;
  /** The focused item's dated history; empty when nothing is focused. */
  marks: readonly ReplayMark[];
}

export function ReplayTimeline({
  histogram,
  asOf,
  today,
  onAsOfChange,
  isPlaying,
  onTogglePlay,
  speed,
  onSpeedChange,
  marks,
}: ReplayTimelineProps) {
  const maxCount = Math.max(1, ...histogram.map((d) => d.count));
  const lastIndex = Math.max(0, histogram.length - 1);
  const shown = asOf ?? today;
  const found = histogram.findIndex((d) => d.date === shown);
  const index = found >= 0 ? found : lastIndex;
  const replaying = asOf !== null;

  function goTo(date: string) {
    onAsOfChange(date >= today ? null : date);
  }

  return (
    <section
      aria-label="Replay timeline"
      className="niuu:w-full niuu:flex niuu:flex-col niuu:gap-2"
    >
      <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
        <p className="niuu-hud-caption">As of {formatDay(shown)}</p>
        <div className="niuu:flex niuu:items-center niuu:gap-3">
          {replaying && (
            <>
              <button
                type="button"
                onClick={onTogglePlay}
                aria-label={isPlaying ? 'Pause' : 'Play'}
                className="niuu-hud-link niuu-hud-caption"
              >
                {isPlaying ? 'Pause' : 'Play'}
              </button>
              <div className="niuu:flex niuu:gap-1" role="group" aria-label="Playback speed">
                {REPLAY_SPEEDS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    aria-pressed={speed === s}
                    onClick={() => onSpeedChange(s)}
                    className={
                      speed === s
                        ? 'niuu-hud-link niuu-hud-caption'
                        : 'niuu-hud-link niuu-hud-caption niuu-hud-caption--muted'
                    }
                  >
                    {s}×
                  </button>
                ))}
              </div>
              <button
                type="button"
                onClick={() => onAsOfChange(null)}
                className="niuu-hud-link niuu-hud-caption"
              >
                Back to now
              </button>
            </>
          )}
          {!replaying && (
            <p className="niuu-hud-caption niuu-hud-caption--muted">Drag back to replay</p>
          )}
        </div>
      </div>

      <div className="niuu-hud-scrubber-bars" role="img" aria-label="Pages first seen per day">
        {histogram.map((day) => (
          <div
            key={day.date}
            title={`${day.date}: ${day.count} page${day.count === 1 ? '' : 's'}`}
            className={
              day.date === shown
                ? 'niuu-hud-scrubber-bar niuu-hud-scrubber-bar--current'
                : 'niuu-hud-scrubber-bar'
            }
            style={{ height: `${(day.count / maxCount) * 100}%` }}
          />
        ))}
      </div>

      <input
        type="range"
        min={0}
        max={lastIndex}
        value={index}
        onChange={(e) => {
          const day = histogram[Number(e.target.value)];
          if (day) goTo(day.date);
        }}
        aria-label="Show memory as of a date"
        aria-valuetext={formatDay(shown)}
        className="niuu-hud-scrubber-range"
      />

      {marks.length > 0 && (
        <ul className="niuu-hud-marks" aria-label="What happened to the focused item">
          {marks.map((mark) => (
            <li key={mark.date}>
              <button
                type="button"
                onClick={() => goTo(mark.date)}
                className={mark.recent ? 'niuu-hud-mark niuu-hud-mark--recent' : 'niuu-hud-mark'}
              >
                {formatDay(mark.date)} · {mark.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
