import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { Field, Select } from '@niuulabs/ui';
import type { IMimirService } from '../ports';
import './DeploymentInspection.css';

export function DeploymentInspection({
  instanceName,
  target,
}: {
  instanceName: string;
  target?: string;
}) {
  const { mounts } = useService<IMimirService>('mimir');
  const client = useQueryClient();
  const [stream, setStream] = useState('');
  const status = useQuery({
    queryKey: ['mimir', 'deployments'],
    queryFn: () => mounts.getDeployments!(),
    enabled: !!mounts.getDeployments,
  });
  const deployment = status.data?.releases.find(
    (item) => item.name === instanceName && (target === undefined || item.target === target),
  );
  const query = useQuery({
    queryKey: ['mimir', 'deployment', instanceName, deployment?.target],
    queryFn: () => mounts.inspectDeployment!(instanceName, deployment?.target),
    enabled: !!deployment && !!mounts.inspectDeployment,
    refetchInterval: 5000,
  });
  const control = useMutation({
    mutationFn: (action: 'start' | 'stop' | 'update') =>
      mounts.controlDeployment!(instanceName, action, deployment?.target),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['mimir'] });
    },
  });
  if (!deployment) return null;
  const runtime = query.data;
  const ready = runtime?.ready ?? deployment.ready;
  const streams = Object.keys(runtime?.logs ?? {});
  const activeStream = streams.includes(stream)
    ? stream
    : (streams.find((key) => runtime?.logs[key]?.trim()) ?? streams[0]);
  const streamLabel = (name: string) =>
    name === 'stderr'
      ? 'Process output (stderr)'
      : name === 'stdout'
        ? 'Standard output (stdout)'
        : name;
  return (
    <section className="mimir-panel instance-runtime" aria-label="Instance runtime">
      <header className="instance-runtime-heading">
        <div>
          <h4>{deployment.backend === 'gbrain' ? 'gbrain runtime' : 'Memory runtime'}</h4>
          <p>
            {deployment.target === 'local' ? 'Local deployment' : 'Cluster deployment'} ·{' '}
            {ready ? 'Ready' : 'Not ready'}
          </p>
        </div>
        {mounts.controlDeployment && (
          <div className="instance-runtime-actions">
            {deployment.can_update && (
              <button
                className="registry-primary"
                disabled={control.isPending}
                onClick={() => control.mutate('update')}
              >
                Update
              </button>
            )}
            <button
              className="registry-primary"
              disabled={control.isPending || ready}
              onClick={() => control.mutate('start')}
            >
              {control.isPending && control.variables === 'start' ? 'Starting…' : 'Start'}
            </button>
            <button
              className="registry-destructive"
              disabled={control.isPending}
              onClick={() => control.mutate('stop')}
            >
              {control.isPending && control.variables === 'stop' ? 'Stopping…' : 'Stop'}
            </button>
          </div>
        )}
      </header>
      {!ready && <p>{runtime?.message ?? deployment.message}</p>}
      {control.error && <p role="alert">{control.error.message}</p>}
      {control.isSuccess && (
        <p role="status">
          {control.variables === 'start'
            ? 'Start requested. Waiting for service readiness.'
            : control.variables === 'update'
              ? 'Update requested. Flux will apply the target’s current chart and image.'
              : 'Stop requested.'}
        </p>
      )}
      {query.error && <p role="alert">Runtime inspection failed: {query.error.message}</p>}
      {query.isPending && <p role="status">Loading runtime details…</p>}
      {runtime && (
        <details className="instance-process-logs">
          <summary>
            Process logs <span>Live output · refreshes every 5 seconds</span>
          </summary>
          {streams.length ? (
            <>
              <Field label="Log stream">
                <Select
                  value={activeStream}
                  onValueChange={setStream}
                  options={streams.map((name) => ({ value: name, label: streamLabel(name) }))}
                />
              </Field>
              <pre role="log">
                {(activeStream && runtime.logs[activeStream]) ||
                  'This stream has no output. A quiet stream does not mean the service is stopped.'}
              </pre>
            </>
          ) : (
            <p>No process log streams are available from this target.</p>
          )}
        </details>
      )}
    </section>
  );
}
