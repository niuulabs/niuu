import { Sparkline } from '@niuulabs/ui';
import { binMetricValues } from '../../hooks/useMetrics';
import type { UseMetricsResult } from '../../hooks/useMetrics';

const CHART_BUCKET_COUNT = 30;

interface MetricChartProps {
  label: string;
  values: number[];
  latest: string;
  unit: string;
  'data-testid'?: string;
}

function MetricChart({ label, values, latest, unit, 'data-testid': testId }: MetricChartProps) {
  return (
    <div
      className="niuu-flex niuu-flex-col niuu-gap-1 niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-p-4"
      data-testid={testId}
    >
      <div className="niuu-flex niuu-items-center niuu-justify-between">
        <span className="niuu-text-sm niuu-text-text-secondary">{label}</span>
        <span className="niuu-font-mono niuu-text-sm niuu-text-text-primary">
          {latest} {unit}
        </span>
      </div>
      <Sparkline values={values} width={320} height={48} />
    </div>
  );
}

interface MetricsTabProps {
  metrics: UseMetricsResult;
}

/** Metrics tab — live sparkline charts for CPU, memory, and GPU. */
export function MetricsTab({ metrics }: MetricsTabProps) {
  const { points, latest } = metrics;

  const cpuValues = binMetricValues(points, (p) => p.cpu, CHART_BUCKET_COUNT);
  const memValues = binMetricValues(points, (p) => p.memMi, CHART_BUCKET_COUNT);
  const gpuValues = binMetricValues(points, (p) => p.gpu, CHART_BUCKET_COUNT);

  const latestCpu = latest ? `${(latest.cpu * 100).toFixed(1)}%` : '—';
  const latestMem = latest ? `${latest.memMi.toFixed(0)}` : '—';
  const latestGpu = latest ? `${latest.gpu.toFixed(2)}` : '—';

  return (
    <div className="niuu-flex niuu-flex-col niuu-gap-4 niuu-p-4" data-testid="metrics-tab">
      {points.length === 0 && (
        <p className="niuu-text-sm niuu-text-text-muted" data-testid="metrics-waiting">
          Waiting for metrics…
        </p>
      )}

      <MetricChart
        label="CPU utilisation"
        values={cpuValues}
        latest={latestCpu}
        unit=""
        data-testid="metrics-cpu"
      />
      <MetricChart
        label="Memory"
        values={memValues}
        latest={latestMem}
        unit="Mi"
        data-testid="metrics-mem"
      />
      <MetricChart
        label="GPU utilisation"
        values={gpuValues}
        latest={latestGpu}
        unit="frac"
        data-testid="metrics-gpu"
      />
    </div>
  );
}
