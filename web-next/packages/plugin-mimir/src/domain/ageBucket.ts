/**
 * Mímir recency buckets — "Colour by · Age" on the Memory Explore view.
 * The single exported source of truth for the bucket boundaries.
 */

export type AgeBucketId = 'today' | 'this-week' | 'this-month' | 'older';

export const AGE_BUCKET_TODAY_DAYS = 1;
export const AGE_BUCKET_WEEK_DAYS = 7;
export const AGE_BUCKET_MONTH_DAYS = 30;

export const AGE_BUCKETS: Array<{ id: AgeBucketId; label: string }> = [
  { id: 'today', label: 'Today' },
  { id: 'this-week', label: 'This week' },
  { id: 'this-month', label: 'This month' },
  { id: 'older', label: 'Older' },
];

const MS_PER_DAY = 24 * 60 * 60 * 1000;

/** Bucket a page's `updatedAt` timestamp relative to `now`. */
export function ageBucket(updatedAt: string, now: Date = new Date()): AgeBucketId {
  const days = (now.getTime() - new Date(updatedAt).getTime()) / MS_PER_DAY;
  if (days < AGE_BUCKET_TODAY_DAYS) return 'today';
  if (days < AGE_BUCKET_WEEK_DAYS) return 'this-week';
  if (days < AGE_BUCKET_MONTH_DAYS) return 'this-month';
  return 'older';
}
