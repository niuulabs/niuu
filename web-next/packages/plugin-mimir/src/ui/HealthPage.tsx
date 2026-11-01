import { useQuery } from '@tanstack/react-query';
import { usePluginCtx, useService } from '@niuulabs/plugin-sdk';
import { Field, Select } from '@niuulabs/ui';
import type { IMimirService } from '../ports';
import { useActiveMount } from '../application/useActiveMount';
import { DoctorPage } from './DoctorPage';
import { LintPage } from './LintPage';

export function HealthPage() {
  const ctx = usePluginCtx();
  const { mounts } = useService<IMimirService>('mimir');
  const { mountName } = useActiveMount();
  const list = useQuery({ queryKey: ['mimir', 'mounts'], queryFn: () => mounts.listMounts() });
  const inspection = useQuery({
    queryKey: ['mimir', 'health-backend', mountName],
    queryFn: () => mounts.inspectInstances!(mountName),
    enabled: !!mountName && !!mounts.inspectInstances,
  });
  const instance = list.data?.find((item) => item.name === mountName);
  return (
    <div data-testid="health-page">
      <section className="mimir-panel niuu:m-6">
        <h3>Instance health</h3>
        <p>
          Check reachability, storage, search, and page integrity for an individual knowledge
          instance.
        </p>
        <Field label="Instance">
          <Select
            value={mountName ?? ''}
            placeholder="Select an instance"
            options={(list.data ?? []).map((item) => ({ value: item.name, label: item.name }))}
            onValueChange={(value) => ctx.setTweak('activeMount', value)}
          />
        </Field>
        {instance && (
          <p>
            {instance.status} · {instance.pages} pages · {instance.sources} sources
          </p>
        )}
        {list.error && <p role="alert">{list.error.message}</p>}
      </section>
      {!mountName ? (
        <p className="niuu:p-6 niuu:text-text-secondary">
          Select an instance to run its health checks.
        </p>
      ) : inspection.isFetching ? (
        <p role="status">Checking backend capabilities…</p>
      ) : inspection.error ? (
        <p role="alert">{inspection.error.message}</p>
      ) : inspection.data?.[0]?.backend === 'gbrain' ? (
        <section className="mimir-panel niuu:m-6">
          <h4>gbrain maintenance</h4>
          <p>
            gbrain uses native dream phases rather than Mimir’s filesystem doctor and lint checks.
            Inspect its runtime for readiness, logs, and recorded phase results.
          </p>
          <button
            className="registry-primary"
            onClick={() => ctx.setTweak('mimir.registryView', 'Analytics')}
          >
            Inspect
          </button>
        </section>
      ) : (
        <>
          <DoctorPage />
          <div className="niuu:border-t niuu:border-border niuu:mt-2" />
          <LintPage />
        </>
      )}
    </div>
  );
}
