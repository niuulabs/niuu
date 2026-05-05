/** A single resource-utilisation data point from a session pod. */
export interface MetricPoint {
  timestamp: number;
  cpu: number;
  memMi: number;
  gpu: number;
}

/** Port for streaming live resource metrics from a running pod. */
export interface IMetricsStream {
  /** Subscribe to metric samples for a session. Returns an unsubscribe function. */
  subscribe(sessionId: string, onMetrics: (point: MetricPoint) => void): () => void;
}
