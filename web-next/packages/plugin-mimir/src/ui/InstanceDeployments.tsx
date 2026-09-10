import { Field, Input, Select } from '@niuulabs/ui';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '../ports';
import type { KnowledgeDeployment } from '../domain/instances';
import './RegistryPage.css';

export function InstanceDeployments() {
  const { mounts } = useService<IMimirService>('mimir');
  const client = useQueryClient();
  const [request, setRequest] = useState<KnowledgeDeployment>({
    name: '',
    backend: 'mimir',
    warden: false,
  });
  const query = useQuery({
    queryKey: ['mimir', 'deployments'],
    queryFn: () => mounts.getDeployments!(),
    enabled: !!mounts.getDeployments,
    refetchInterval: 10000,
  });
  const targets =
    query.data?.targets ?? (query.data ? [{ ...query.data, id: query.data.target ?? '' }] : []);
  const target = targets.find((item) => item.id === request.target) ?? targets[0];
  const deploy = useMutation({
    mutationFn: () => mounts.deployInstance!({ ...request, target: target?.id }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['mimir'] });
    },
  });
  return (
    <section aria-label="Instance deployments" className="mimir-panel niuu:mb-6">
      <span className="registry-engine-label">YOUR KNOWLEDGE, WHERE YOU NEED IT</span>
      <h3>Deploy a knowledge instance</h3>
      <p className="niuu:text-sm niuu:text-text-secondary">
        Choose an engine and a home. Storage and runtime configuration come from your deployment
        target.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          deploy.mutate();
        }}
      >
        <div className="niuu:grid niuu:grid-cols-2 niuu:gap-4 niuu:my-5">
          {(['mimir', 'gbrain'] as const).map((backend) => (
            <button
              key={backend}
              type="button"
              aria-pressed={request.backend === backend}
              className="mimir-instance-card"
              onClick={() =>
                setRequest({
                  ...request,
                  backend,
                  warden: false,
                  warden_overrides: undefined,
                  dream: undefined,
                })
              }
            >
              <span className="registry-engine-icon" aria-hidden="true">
                {backend === 'mimir' ? '▤' : '✧'}
              </span>
              <strong>{backend === 'mimir' ? 'Mimir' : 'gbrain'}</strong>
              <span>
                {backend === 'mimir'
                  ? 'A source-backed wiki, with an optional warden to keep it fresh.'
                  : 'Connected memory, with optional native dream cycles.'}
              </span>
            </button>
          ))}
        </div>
        {query.isPending && mounts.getDeployments && (
          <p role="status">Finding deployment targets…</p>
        )}
        {(query.error || !mounts.getDeployments) && (
          <div role="alert">
            <p>Deployment targets could not be loaded.</p>
            <p>{query.error?.message ?? 'This connection does not expose deployment controls.'}</p>
            <button type="button" onClick={() => void query.refetch()}>
              Check again
            </button>
          </div>
        )}
        {target && (
          <>
            <div className="niuu:grid niuu:grid-cols-2 niuu:gap-4 niuu:my-4">
              <Field label="Instance name">
                <Input
                  required
                  pattern="[a-z][a-z0-9-]{0,39}"
                  placeholder="team-knowledge"
                  value={request.name}
                  onChange={(event) => setRequest({ ...request, name: event.target.value })}
                />
              </Field>
              <Field label="Deploy to">
                <Select
                  value={target.id}
                  onValueChange={(value) =>
                    setRequest({
                      ...request,
                      target: value,
                      warden: false,
                      warden_overrides: undefined,
                      dream: undefined,
                    })
                  }
                  options={targets.map((item) => ({
                    value: item.id,
                    label:
                      item.id === 'local' || item.target === 'local'
                        ? 'This computer'
                        : item.cluster,
                  }))}
                />
              </Field>
            </div>
            {'error' in target && target.error && <p role="alert">{target.error}</p>}
            <fieldset className="niuu:p-4 niuu:my-4 niuu:border niuu:border-border-subtle niuu:rounded-md">
              <legend>Maintenance</legend>
              {request.backend === 'mimir' && (
                <>
                  <label className="niuu:flex niuu:items-center niuu:gap-3">
                    <input
                      type="checkbox"
                      style={{ appearance: 'auto', width: 16, height: 16 }}
                      disabled={!target.warden_available}
                      checked={request.warden ?? false}
                      onChange={(event) =>
                        setRequest({
                          ...request,
                          warden: event.target.checked,
                          warden_overrides: undefined,
                        })
                      }
                    />
                    Attach a warden
                  </label>
                  <p className="niuu:text-sm niuu:text-text-muted">
                    {target.warden_available
                      ? 'A warden keeps this wiki up to date by reviewing sources, refreshing stale pages, and running scheduled maintenance. Its model, persona, schedules, and credentials come from this target’s warden profile; you can override the model, persona, and cadence below. Credentials stay with the target.'
                      : 'This target needs a configured warden profile before it can run a warden.'}
                  </p>
                </>
              )}
              {request.backend === 'mimir' && request.warden && (
                <div className="niuu:grid niuu:grid-cols-2 niuu:gap-4 niuu:my-4">
                  {(
                    [
                      ['model', 'Model', 'text'],
                      ['persona', 'Persona', 'text'],
                      ['dream_cycle_cron_expression', 'Warden dream schedule (cron)', 'text'],
                      [
                        'source_trigger_poll_interval_seconds',
                        'Source check interval (seconds)',
                        'number',
                      ],
                      [
                        'staleness_trigger_schedule_hours',
                        'Staleness check interval (hours)',
                        'number',
                      ],
                    ] as const
                  ).map(([key, label, type]) => (
                    <Field key={key} label={label}>
                      <Input
                        type={type}
                        placeholder="Use target default"
                        min={key === 'source_trigger_poll_interval_seconds' ? 10 : 1}
                        value={request.warden_overrides?.[key] ?? ''}
                        onChange={(event) =>
                          setRequest({
                            ...request,
                            warden_overrides: {
                              ...request.warden_overrides,
                              [key]:
                                event.target.value === ''
                                  ? undefined
                                  : type === 'number'
                                    ? Number(event.target.value)
                                    : event.target.value,
                            },
                          })
                        }
                      />
                    </Field>
                  ))}
                </div>
              )}
              {request.backend === 'gbrain' && (
                <>
                  <label className="niuu:flex niuu:items-center niuu:gap-3">
                    <input
                      type="checkbox"
                      style={{ appearance: 'auto', width: 16, height: 16 }}
                      disabled={target.dream_available === false}
                      checked={request.dream?.enabled ?? false}
                      onChange={(event) =>
                        setRequest({
                          ...request,
                          dream: {
                            enabled: event.target.checked,
                            schedule: '0 2 * * *',
                            phases: ['lint', 'backlinks', 'orphans'],
                          },
                        })
                      }
                    />
                    Enable scheduled dream cycles
                  </label>
                  <p className="niuu:text-sm niuu:text-text-secondary">
                    Dream cycles run background maintenance on gbrain’s memory: checking pages,
                    updating backlinks, and finding unconnected notes. Available work depends on the
                    instance’s storage and enabled phases.
                  </p>
                  {target.dream_available === false && (
                    <p className="niuu:text-sm niuu:text-text-muted">
                      Native scheduling needs PostgreSQL on this target so dreams can run alongside
                      the server.
                    </p>
                  )}
                  {request.dream?.enabled && (
                    <Field
                      label="Schedule (cron, UTC)"
                      hint="Runs lint, backlinks, and orphan maintenance."
                    >
                      <Input
                        required
                        value={request.dream.schedule}
                        onChange={(event) =>
                          setRequest({
                            ...request,
                            dream: { ...request.dream!, schedule: event.target.value },
                          })
                        }
                      />
                    </Field>
                  )}
                </>
              )}
            </fieldset>
            {!target.backends.includes(request.backend) && (
              <p role="alert">{request.backend} is not configured on this target.</p>
            )}
            <button
              className="registry-primary"
              disabled={deploy.isPending || !target.backends.includes(request.backend)}
              type="submit"
            >
              {deploy.isPending ? 'Deploying…' : 'Deploy instance'}
            </button>
          </>
        )}
      </form>
      {deploy.error && <p role="alert">Deployment failed: {deploy.error.message}</p>}
      {deploy.isSuccess && (
        <p role="status">
          Deployment requested. Readiness and maintenance are available in Analytics.
        </p>
      )}
    </section>
  );
}
