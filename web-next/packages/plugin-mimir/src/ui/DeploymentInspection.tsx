import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { Field, Select } from '@niuulabs/ui';
import type { IMimirService } from '../ports';
import './DeploymentInspection.css';

export function DeploymentInspection({ instanceName }: { instanceName: string }) {
  const { mounts } = useService<IMimirService>('mimir');
  const client = useQueryClient();
  const [stream, setStream] = useState('');
  const status = useQuery({
    queryKey: ['mimir', 'deployments'],
    queryFn: () => mounts.getDeployments!(),
    enabled: !!mounts.getDeployments,
  });
  const deployment = status.data?.releases.find((item) => item.name === instanceName);
  const query = useQuery({
    queryKey: ['mimir', 'deployment', instanceName, deployment?.target],
    queryFn: () => mounts.inspectDeployment!(instanceName, deployment?.target),
    enabled: !!deployment && !!mounts.inspectDeployment,
    refetchInterval: 5000,
  });
  const control = useMutation({
    mutationFn: (action: 'start' | 'stop') =>
      mounts.controlDeployment!(instanceName, action, deployment?.target),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['mimir'] });
    },
  });
  if (!deployment) return null;
  const runtime = query.data;
  const ready = runtime?.ready ?? deployment.ready;
  const streams = Object.keys(runtime?.logs ?? {}).filter((name) => name !== 'dream');
  const activeStream = streams.includes(stream)
    ? stream
    : (streams.find((key) => runtime?.logs[key]?.trim()) ?? streams[0]);
  const results = runtime?.dream_results ?? [];
  const latest = [...results].sort((a, b) => b.timestamp.localeCompare(a.timestamp))[0];
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
          <h4>
            {deployment.backend === 'gbrain' ? 'gbrain runtime & dream cycles' : 'Mimir runtime'}
          </h4>
          <p>
            {deployment.target === 'local' ? 'Local deployment' : 'Cluster deployment'} ·{' '}
            {ready ? 'Ready' : 'Not ready'}
          </p>
        </div>
        {mounts.controlDeployment && (
          <div className="instance-runtime-actions">
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
            : 'Stop requested.'}
        </p>
      )}
      {query.error && <p role="alert">Runtime inspection failed: {query.error.message}</p>}
      {query.isPending && <p role="status">Loading runtime details…</p>}
      {runtime && deployment.backend === 'gbrain' && (
        <section className="instance-dreams" aria-label="Native dream cycles">
          <div className="instance-runtime-heading">
            <div>
              <h4>Native dream cycles</h4>
              <p>Background maintenance performed by gbrain.</p>
            </div>
            <span>{runtime.dream?.enabled ? 'Scheduled' : 'Not scheduled'}</span>
          </div>
          {runtime.dream?.enabled && (
            <dl className="instance-runtime-facts">
              <div>
                <dt>Schedule (UTC)</dt>
                <dd>{runtime.dream.schedule}</dd>
              </div>
              <div>
                <dt>Enabled phases</dt>
                <dd>{runtime.dream.phases.join(', ')}</dd>
              </div>
            </dl>
          )}
          {latest ? (
            <>
              <div className="instance-runtime-heading">
                <h4>Latest recorded cycle</h4>
                <span>
                  {new Date(latest.timestamp).toLocaleString()} · {latest.status}
                  {latest.duration_ms !== undefined ? ` · ${latest.duration_ms} ms` : ''}
                </span>
              </div>
              <ul className="instance-dream-phases">
                {latest.phases.map((phase) => (
                  <li key={phase.phase}>
                    <div>
                      <strong>{phase.phase}</strong>
                      <span data-status={phase.status}>{phase.status}</span>
                    </div>
                    <p>
                      {phase.details?.reason === 'no_brain_dir'
                        ? 'Skipped: no filesystem checkout is attached to this instance.'
                        : phase.summary}
                    </p>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p>
              No completed dream result has been recorded in the available logs.
              {runtime.dream?.enabled ? ' Results will appear after a cycle runs.' : ''}
            </p>
          )}
          {runtime.logs.dream && (
            <details>
              <summary>Raw dream output</summary>
              <pre role="log">{runtime.logs.dream}</pre>
            </details>
          )}
        </section>
      )}
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
