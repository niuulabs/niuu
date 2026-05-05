import { describe, it, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { createElement } from 'react';
import type { ReactNode } from 'react';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { useMetrics, binMetricValues } from './useMetrics';
import type { MetricPoint } from '../../ports/IMetricsStream';
import type { IMetricsStream } from '../../ports/IMetricsStream';

function makePoints(cpus: number[]): MetricPoint[] {
  return cpus.map((cpu, i) => ({ timestamp: i * 1000, cpu, memMi: cpu * 100, gpu: 0 }));
}

describe('binMetricValues', () => {
  it('returns all zeros for empty points', () => {
    const result = binMetricValues([], (p) => p.cpu, 5);
    expect(result).toHaveLength(5);
    expect(result.every((v) => v === 0)).toBe(true);
  });

  it('returns bucketCount buckets', () => {
    const points = makePoints([0.1, 0.2, 0.3, 0.4, 0.5, 0.6]);
    const result = binMetricValues(points, (p) => p.cpu, 3);
    expect(result).toHaveLength(3);
  });

  it('averages values within each bucket', () => {
    // 4 points, 2 buckets → each bucket has 2 points
    const points = makePoints([0.0, 1.0, 0.5, 0.5]);
    const result = binMetricValues(points, (p) => p.cpu, 2);
    expect(result[0]).toBeCloseTo(0.5, 5); // (0.0 + 1.0) / 2
    expect(result[1]).toBeCloseTo(0.5, 5); // (0.5 + 0.5) / 2
  });

  it('handles a single point with bucketCount > 1', () => {
    const points = makePoints([0.42]);
    const result = binMetricValues(points, (p) => p.cpu, 4);
    expect(result).toHaveLength(4);
    expect(result[0]).toBeCloseTo(0.42, 5);
    // Remaining buckets have no data → 0
    expect(result[1]).toBe(0);
    expect(result[2]).toBe(0);
    expect(result[3]).toBe(0);
  });

  it('works with memMi accessor', () => {
    const points = makePoints([0.5, 0.5]);
    const result = binMetricValues(points, (p) => p.memMi, 1);
    // memMi = cpu * 100 = 50
    expect(result[0]).toBeCloseTo(50, 1);
  });

  it('returns a single bucket equal to average of all points', () => {
    const points = makePoints([0.2, 0.4, 0.6]);
    const result = binMetricValues(points, (p) => p.cpu, 1);
    expect(result).toHaveLength(1);
    expect(result[0]).toBeCloseTo(0.4, 5);
  });
});

function makeStreamWrapper(stream: IMetricsStream) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(ServicesProvider, { services: { metricsStream: stream } }, children);
  };
}

describe('useMetrics', () => {
  it('starts with empty points and undefined latest', () => {
    const stream: IMetricsStream = { subscribe: vi.fn().mockReturnValue(() => {}) };
    const { result } = renderHook(() => useMetrics('sess-1'), {
      wrapper: makeStreamWrapper(stream),
    });

    expect(result.current.points).toEqual([]);
    expect(result.current.latest).toBeUndefined();
  });

  it('subscribes with the given sessionId', () => {
    const subscribe = vi.fn().mockReturnValue(() => {});
    const stream: IMetricsStream = { subscribe };
    renderHook(() => useMetrics('sess-42'), { wrapper: makeStreamWrapper(stream) });

    expect(subscribe).toHaveBeenCalledWith('sess-42', expect.any(Function));
  });

  it('accumulates metric points from the stream callback', () => {
    let callback: ((point: MetricPoint) => void) | undefined;
    const stream: IMetricsStream = {
      subscribe: vi.fn((_id, cb) => {
        callback = cb;
        return () => {};
      }),
    };

    const { result } = renderHook(() => useMetrics('sess-1'), {
      wrapper: makeStreamWrapper(stream),
    });

    const point: MetricPoint = { timestamp: 1000, cpu: 0.5, memMi: 256, gpu: 0 };
    act(() => {
      callback!(point);
    });

    expect(result.current.points).toHaveLength(1);
    expect(result.current.points[0]).toEqual(point);
    expect(result.current.latest).toEqual(point);
  });

  it('trims points beyond MAX_HISTORY_POINTS (60)', () => {
    let callback: ((point: MetricPoint) => void) | undefined;
    const stream: IMetricsStream = {
      subscribe: vi.fn((_id, cb) => {
        callback = cb;
        return () => {};
      }),
    };

    const { result } = renderHook(() => useMetrics('sess-1'), {
      wrapper: makeStreamWrapper(stream),
    });

    // Push 65 points
    act(() => {
      for (let i = 0; i < 65; i++) {
        callback!({ timestamp: i * 1000, cpu: i / 100, memMi: i, gpu: 0 });
      }
    });

    expect(result.current.points).toHaveLength(60);
    // The first 5 should have been trimmed, so the first point should be i=5
    expect(result.current.points[0]?.timestamp).toBe(5000);
    expect(result.current.latest?.timestamp).toBe(64000);
  });

  it('calls the unsubscribe function on unmount', () => {
    const unsub = vi.fn();
    const stream: IMetricsStream = { subscribe: vi.fn().mockReturnValue(unsub) };

    const { unmount } = renderHook(() => useMetrics('sess-1'), {
      wrapper: makeStreamWrapper(stream),
    });

    unmount();
    expect(unsub).toHaveBeenCalled();
  });
});
