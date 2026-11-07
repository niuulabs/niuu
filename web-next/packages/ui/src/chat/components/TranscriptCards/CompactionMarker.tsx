import { formatTokenCount } from './formatTokenCount';
import './TranscriptCards.css';

export interface CompactionMarkerProps {
  tokensBefore?: number;
  tokensAfter?: number;
}

function figure(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : undefined;
}

/**
 * The token figures a compaction part carries. Muse sends `tokensBefore` / `tokensAfter`
 * on the item (and `pre_tokens` / `post_tokens` in `compact_metadata`); other runtimes
 * send none, and then the marker shows no figures.
 */
export function compactionTokens(part: Record<string, unknown>): CompactionMarkerProps {
  return {
    tokensBefore: figure(part.tokensBefore ?? part.pre_tokens),
    tokensAfter: figure(part.tokensAfter ?? part.post_tokens),
  };
}

/** A centred rule where the runtime folded the earlier conversation into a summary. */
export function CompactionMarker({ tokensBefore, tokensAfter }: CompactionMarkerProps) {
  const figures =
    tokensBefore !== undefined && tokensAfter !== undefined
      ? ` · ${formatTokenCount(tokensBefore)} → ${formatTokenCount(tokensAfter)} tokens`
      : '';
  return (
    <div className="niuu-chat-compaction" role="separator" data-testid="compaction-marker">
      Context compacted{figures}
    </div>
  );
}
