import { ErrorState, LoadingState } from '@niuulabs/ui';
import { CliBadge } from './atoms';
import { useLaunchSpecs } from './useLaunchSpecs';
import type { VolundrLaunchSpec } from '../models/volundr.model';

function formatResources(spec: VolundrLaunchSpec): string {
  const cpu = spec.resourceConfig.cpu ? `cpu ${spec.resourceConfig.cpu}` : '';
  const memory = spec.resourceConfig.memory ? `mem ${spec.resourceConfig.memory}` : '';
  const gpu =
    spec.resourceConfig.gpu && spec.resourceConfig.gpu !== '0'
      ? `gpu ${spec.resourceConfig.gpu}`
      : '';
  return [cpu, memory, gpu].filter(Boolean).join(' · ') || 'default resources';
}

function formatSource(spec: VolundrLaunchSpec): string {
  if (!spec.source) return 'source selected at launch';
  if (spec.source.type === 'local_mount') {
    return spec.source.local_path ?? spec.source.paths[0]?.host_path ?? 'local mount';
  }
  return [spec.source.repo, spec.source.branch].filter(Boolean).join(' @ ');
}

function LaunchSpecCard({ spec }: { spec: VolundrLaunchSpec }) {
  return (
    <article className="niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-4">
      <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-3">
        <div className="niuu:min-w-0">
          <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2">
            <CliBadge cli={spec.cliTool} compact />
            <h3 className="niuu:truncate niuu:font-mono niuu:text-sm niuu:text-text-primary">
              {spec.name}
            </h3>
            {spec.isDefault ? (
              <span className="niuu:rounded-sm niuu:border niuu:border-border-subtle niuu:px-1.5 niuu:py-0.5 niuu:font-mono niuu:text-[10px] niuu:uppercase niuu:text-text-muted">
                default
              </span>
            ) : null}
          </div>
          <p className="niuu:mt-2 niuu:text-sm niuu:text-text-muted">
            {spec.description || 'No description configured.'}
          </p>
        </div>
        <span className="niuu:rounded-sm niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-2 niuu:py-1 niuu:font-mono niuu:text-[11px] niuu:uppercase niuu:text-text-muted">
          {spec.scope}
        </span>
      </div>
      <dl className="niuu:mt-4 niuu:grid niuu:grid-cols-2 niuu:gap-3 niuu:text-xs">
        <div>
          <dt className="niuu:text-text-faint">Runtime</dt>
          <dd className="niuu:mt-1 niuu:text-text-secondary">
            {spec.sessionDefinition ?? spec.workloadType}
          </dd>
        </div>
        <div>
          <dt className="niuu:text-text-faint">Model</dt>
          <dd className="niuu:mt-1 niuu:text-text-secondary">
            {spec.model ?? 'selected at launch'}
          </dd>
        </div>
        <div>
          <dt className="niuu:text-text-faint">Resources</dt>
          <dd className="niuu:mt-1 niuu:text-text-secondary">{formatResources(spec)}</dd>
        </div>
        <div>
          <dt className="niuu:text-text-faint">Source</dt>
          <dd className="niuu:mt-1 niuu:truncate niuu:text-text-secondary">{formatSource(spec)}</dd>
        </div>
      </dl>
      {spec.mcpServers.length > 0 ? (
        <div className="niuu:mt-4 niuu:flex niuu:flex-wrap niuu:gap-2">
          {spec.mcpServers.map((server) => (
            <span
              key={server.name}
              className="niuu:rounded-sm niuu:bg-bg-primary niuu:px-2 niuu:py-1 niuu:font-mono niuu:text-[11px] niuu:text-text-muted"
            >
              {server.name}
            </span>
          ))}
        </div>
      ) : null}
    </article>
  );
}

export function LaunchCatalogPage() {
  const launchSpecs = useLaunchSpecs();
  const specs = launchSpecs.data ?? [];

  if (launchSpecs.isLoading) return <LoadingState label="Loading launch catalog..." />;
  if (launchSpecs.isError) {
    return (
      <ErrorState
        title="Failed to load launch catalog"
        message={launchSpecs.error instanceof Error ? launchSpecs.error.message : 'Unknown error'}
      />
    );
  }

  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-5" data-testid="launch-catalog-page">
      <header>
        <h2 className="niuu:text-lg niuu:font-semibold niuu:text-text-primary">Launch Catalog</h2>
        <p className="niuu:mt-1 niuu:text-sm niuu:text-text-muted">
          Preloaded catalog specs and saved user launch specs exposed by Völundr.
        </p>
      </header>
      {specs.length === 0 ? (
        <div className="niuu:rounded-lg niuu:border niuu:border-dashed niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-6 niuu:text-sm niuu:text-text-muted">
          No launch specs configured yet.
        </div>
      ) : (
        <div className="niuu:grid niuu:grid-cols-1 niuu:gap-3 xl:niuu:grid-cols-2">
          {specs.map((spec) => (
            <LaunchSpecCard key={`${spec.scope}:${spec.id ?? spec.name}`} spec={spec} />
          ))}
        </div>
      )}
    </div>
  );
}
