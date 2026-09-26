/** ReplayTimeline — bottom timeline for Replay mode: per-day histogram, play/pause, speed, and a native scrubber. */
import { REPLAY_SPEEDS, type DayCount, type ReplaySpeed } from '../../domain/replayHistogram';

export interface ReplayTimelineProps {
  histogram: DayCount[];
  asOf: string;
  onAsOfChange: (date: string) => void;
  isPlaying: boolean;
  onTogglePlay: () => void;
  speed: ReplaySpeed;
  onSpeedChange: (speed: ReplaySpeed) => void;
}

export function ReplayTimeline({
  histogram,
  asOf,
  onAsOfChange,
  isPlaying,
  onTogglePlay,
  speed,
  onSpeedChange,
}: ReplayTimelineProps) {
  const maxCount = Math.max(1, ...histogram.map((d) => d.count));
  const index = Math.max(
    0,
    histogram.findIndex((d) => d.date === asOf),
  );

  return (
    <section
      className="niuu:w-full niuu:max-w-3xl niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:p-4 niuu:flex niuu:flex-col niuu:gap-2"
      aria-label="Replay timeline"
    >
      <div className="niuu:flex niuu:items-center niuu:gap-3">
        <button
          type="button"
          onClick={onTogglePlay}
          aria-label={isPlaying ? 'Pause' : 'Play'}
          className="niuu:w-8 niuu:h-8 niuu:flex niuu:items-center niuu:justify-center niuu:rounded-full niuu:bg-brand niuu:text-bg-primary"
        >
          {isPlaying ? '❚❚' : '▶'}
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
                  ? 'niuu:px-2 niuu:py-0.5 niuu:text-xs niuu:rounded-sm niuu:bg-bg-tertiary niuu:text-text-primary'
                  : 'niuu:px-2 niuu:py-0.5 niuu:text-xs niuu:rounded-sm niuu:text-text-muted'
              }
            >
              {s}×
            </button>
          ))}
        </div>
      </div>

      <div
        className="niuu:flex niuu:items-end niuu:gap-px niuu:h-12"
        role="img"
        aria-label="Pages known per day"
      >
        {histogram.map((day) => (
          <div
            key={day.date}
            title={`${day.date}: ${day.count} page${day.count === 1 ? '' : 's'}`}
            className={
              day.date === asOf
                ? 'niuu:flex-1 niuu:bg-brand-300'
                : 'niuu:flex-1 niuu:bg-bg-elevated'
            }
            style={{ height: `${Math.max(4, (day.count / maxCount) * 100)}%` }}
          />
        ))}
      </div>

      <input
        type="range"
        min={0}
        max={Math.max(0, histogram.length - 1)}
        value={index}
        onChange={(e) => {
          const day = histogram[Number(e.target.value)];
          if (day) onAsOfChange(day.date);
        }}
        aria-label="Replay date"
        className="niuu:w-full"
      />

      <div className="niuu:flex niuu:justify-between niuu:text-xs niuu:text-text-muted niuu:font-mono">
        <span>{histogram[0]?.date ?? ''}</span>
        <span>{histogram[histogram.length - 1]?.date ?? ''}</span>
      </div>
    </section>
  );
}
