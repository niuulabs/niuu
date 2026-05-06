import { Sparkline } from '@niuulabs/ui';

const HISTORY_COUNT = 10;
const SCOPE_ADHERENCE = 0.94;
const TEST_COVERAGE = '98%';

/**
 * Generate a deterministic confidence history array from a saga ID.
 * Uses a sine-based walk so the sparkline looks plausible and consistent
 * across renders, matching the web2 reference pattern.
 */
function generateHistory(sagaId: string): number[] {
  let h = 0;
  for (const c of sagaId) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return Array.from({ length: HISTORY_COUNT }, (_, i) => {
    const progress = i / (HISTORY_COUNT - 1);
    const noise = Math.sin(h + i * 2.3) * 0.06;
    return Math.max(0.05, Math.min(0.99, 0.4 + progress * 0.42 + noise));
  });
}

interface ConfidenceDriftCardProps {
  sagaId: string;
  /** Current aggregate confidence score (0–100). */
  confidence: number;
}

export function ConfidenceDriftCard({ sagaId, confidence }: ConfidenceDriftCardProps) {
  const history = generateHistory(sagaId);
  const startVal = history[0] ?? 0;
  const currentVal = confidence / 100;
  // Splice actual current confidence into the last data point for accuracy.
  const values = [...history.slice(0, -1), currentVal];

  return (
    <section
      aria-label="Confidence drift"
      className="niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-overflow-hidden"
    >
      <div className="niuu-flex niuu-items-center niuu-justify-between niuu-px-5 niuu-py-4 niuu-border-b niuu-border-border-subtle">
        <h3 className="niuu-m-0 niuu-text-[17px] niuu-font-semibold niuu-text-text-primary">
          Confidence drift
        </h3>
        <span className="niuu-font-mono niuu-text-[12px] niuu-text-text-muted">
          aggregate · {values.length} events
        </span>
      </div>

      <div className="niuu-p-5 niuu-space-y-4">
        <p className="niuu-m-0 niuu-text-[13px] niuu-leading-6 niuu-text-text-secondary">
          How this saga&apos;s overall confidence has moved as raids reported back. Dips call for
          attention — a QA fail or security flag will pull it down.
        </p>
        <div className="niuu-rounded-lg niuu-bg-[#15191e] niuu-p-3">
          <Sparkline values={values} id={sagaId} height={126} />
        </div>
        <div
          className="niuu-flex niuu-flex-wrap niuu-gap-4 niuu-font-mono niuu-text-[12px] niuu-text-text-muted"
          aria-label="Confidence metrics"
        >
          <span>
            start <strong>{startVal.toFixed(2)}</strong>
          </span>
          <span>
            now <strong className="niuu-text-text-primary">{currentVal.toFixed(2)}</strong>
          </span>
          <span>
            scope_adherence <strong>{SCOPE_ADHERENCE}</strong>
          </span>
          <span>
            tests <strong>{TEST_COVERAGE}</strong>
          </span>
        </div>
      </div>
    </section>
  );
}
