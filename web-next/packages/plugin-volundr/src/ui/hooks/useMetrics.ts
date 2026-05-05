import { useState, useEffect } from 'react';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMetricsStream, MetricPoint } from '../../ports/IMetricsStream';

const MAX_HISTORY_POINTS = 60;

/** Return value of useMetrics. */
export interface UseMetricsResult {
  points: MetricPoint[];
  latest: MetricPoint | undefined;
}

/**
 * Subscribes to the metrics stream for a session and maintains a rolling
 * window of the last MAX_HISTORY_POINTS data points.
 */
export function useMetrics(sessionId: string): UseMetricsResult {
  const stream = useService<IMetricsStream>('metricsStream');
  const [points, setPoints] = useState<MetricPoint[]>([]);

  useEffect(() => {
    const unsub = stream.subscribe(sessionId, (point) => {
      setPoints((prev) => {
        const next = [...prev, point];
        return next.length > MAX_HISTORY_POINTS
          ? next.slice(next.length - MAX_HISTORY_POINTS)
          : next;
      });
    });
    return unsub;
  }, [sessionId, stream]);

  return { points, latest: points[points.length - 1] };
}

/** Bin metric values into N buckets for chart display. */
export function binMetricValues(
  points: MetricPoint[],
  accessor: (p: MetricPoint) => number,
  bucketCount: number,
): number[] {
  if (points.length === 0) return Array(bucketCount).fill(0) as number[];
  const bucketSize = Math.ceil(points.length / bucketCount);
  const buckets: number[] = [];
  for (let i = 0; i < bucketCount; i++) {
    const slice = points.slice(i * bucketSize, (i + 1) * bucketSize);
    const avg = slice.length > 0 ? slice.reduce((s, p) => s + accessor(p), 0) / slice.length : 0;
    buckets.push(avg);
  }
  return buckets;
}
