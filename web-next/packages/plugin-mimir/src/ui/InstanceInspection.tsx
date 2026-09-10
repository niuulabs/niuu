import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '../ports';
import { useActiveMount } from '../application/useActiveMount';
import { categoryColor } from './graphColors';
import './InstanceInspection.css';
import { RavnsPage } from './RavnsPage';
import { DeploymentInspection } from './DeploymentInspection';

export function InstanceInspection() {
  const { mounts, pages } = useService<IMimirService>('mimir');
  const { mountName } = useActiveMount();
  const [selected, setSelected] = useState('');
  const query = useQuery({
    queryKey: ['mimir', 'inspection', mountName],
    queryFn: () => mounts.inspectInstances!(mountName),
    enabled: !!mounts.inspectInstances,
  });
  const instance = query.data?.find((item) => item.mount === selected) ?? query.data?.[0];
  const graph = useQuery({
    queryKey: ['mimir', 'instance-graph', instance?.mount],
    queryFn: () => pages.getGraph({ mountName: instance!.mount }),
    enabled: !!instance,
  });
  if (!mounts.inspectInstances)
    return <p>Instance inspection is not supported by this connection.</p>;
  if (query.isPending) return <p role="status">Loading your knowledge instances…</p>;
  if (query.error) return <p role="alert">Could not inspect instances: {query.error.message}</p>;
  const categories = new Map<string, number>();
  const relationships = new Map<string, number>();
  graph.data?.nodes.forEach((node) =>
    categories.set(node.category, (categories.get(node.category) ?? 0) + 1),
  );
  graph.data?.edges.forEach((edge) =>
    relationships.set(edge.type ?? 'related', (relationships.get(edge.type ?? 'related') ?? 0) + 1),
  );
  const connected = new Set(graph.data?.edges.flatMap((edge) => [edge.source, edge.target]));
  const total = graph.data?.nodes.length ?? 0;
  const isolated = graph.data?.nodes.filter((node) => !connected.has(node.id)).length ?? 0;
  const bars = [...categories].sort((a, b) => b[1] - a[1]);
  return (
    <section aria-label="Instance inspection" className="mimir-insights">
      <div className="mimir-instance-cards" aria-label="Choose an instance">
        {query.data.map((item) => (
          <button
            type="button"
            key={item.mount}
            aria-pressed={item.mount === instance?.mount}
            onClick={() => setSelected(item.mount)}
            className="mimir-instance-card"
          >
            <span className="mimir-eyebrow">{item.backend}</span>
            <strong>
              {item.mount} · {item.backend}
            </strong>
            <span className="mimir-card-stats">
              <b>{item.metrics.Pages ?? '—'}</b> pages{' '}
              <span>{item.metrics['Storage engine'] ?? 'Markdown'}</span>
            </span>
            <span className="mimir-card-footer">
              {item.metrics.Health ? `Health: ${item.metrics.Health}` : 'Connected'}{' '}
              <span>{item.mount === instance?.mount ? 'Viewing overview ↗' : 'Inspect →'}</span>
            </span>
          </button>
        ))}
      </div>
      {!instance && <p>No instances are attached.</p>}
      {instance && (
        <div className="mimir-instance-dashboard" aria-label={`${instance.mount} overview`}>
          <header className="mimir-dashboard-heading">
            <div>
              <span className="mimir-eyebrow">Instance overview</span>
              <h3>{instance.mount}</h3>
              <p>
                {instance.backend}
                {instance.metrics.Version ? ` · v${instance.metrics.Version}` : ''}
              </p>
            </div>
            <span className="mimir-backend-badge">
              {instance.metrics['Storage engine'] ?? instance.backend}
            </span>
          </header>
          <div className="mimir-kpis">
            {[
              ['Pages', instance.metrics.Pages ?? '—', 'Stored knowledge'],
              ['Connections', graph.data?.edges.length ?? '—', 'Explicit and shared-source links'],
              ['Categories', graph.data ? categories.size : '—', 'From this instance'],
              ['Unlinked pages', graph.data ? isolated : '—', 'No links in this graph'],
            ].map(([label, value, hint]) => (
              <div className="mimir-panel" key={label}>
                <span className="mimir-eyebrow">{label}</span>
                <strong className="mimir-kpi-value">{value}</strong>
                <small>{hint}</small>
              </div>
            ))}
          </div>
          {graph.isPending && <p role="status">Loading instance graph…</p>}
          {graph.error && <p role="alert">Graph statistics unavailable: {graph.error.message}</p>}
          <div className="mimir-chart-grid">
            <section className="mimir-panel">
              <h4>Knowledge composition</h4>
              <p>Pages by category</p>
              {bars.map(([category, count]) => (
                <div className="mimir-bar-row" key={category}>
                  <div>
                    <span>{category}</span>
                    <b>{count}</b>
                  </div>
                  <div className="mimir-bar-track">
                    <span
                      style={{
                        width: `${total ? (count / total) * 100 : 0}%`,
                        background: categoryColor(category),
                      }}
                    />
                  </div>
                </div>
              ))}
              {graph.data && !bars.length && (
                <p>No pages yet. Ingest a source to start building this instance.</p>
              )}
            </section>
            <section className="mimir-panel">
              <h4>Relationship coverage</h4>
              <p>How much of the knowledge is connected</p>
              <div className="mimir-coverage">
                <svg
                  viewBox="0 0 120 120"
                  role="img"
                  aria-label={
                    graph.data
                      ? `${total - isolated} of ${total} pages connected`
                      : 'Connection coverage unavailable'
                  }
                >
                  <circle className="mimir-ring-track" cx="60" cy="60" r="46" />
                  <circle
                    className="mimir-ring-value"
                    cx="60"
                    cy="60"
                    r="46"
                    pathLength="100"
                    strokeDasharray={`${total ? ((total - isolated) / total) * 100 : 0} 100`}
                  />
                  <text x="60" y="64" textAnchor="middle">
                    {graph.data && total
                      ? `${Math.round(((total - isolated) / total) * 100)}%`
                      : '—'}
                  </text>
                </svg>
                <div>
                  <strong>{graph.data ? total - isolated : '—'} connected pages</strong>
                  <p>{graph.data ? isolated : '—'} without a visible relationship</p>
                </div>
              </div>
              {[...relationships].map(([type, count]) => (
                <div className="mimir-detail-row" key={type}>
                  <span>{type.replaceAll('_', ' ')}</span>
                  <b>{count}</b>
                </div>
              ))}
            </section>
          </div>
          <div className="mimir-chart-grid">
            <section className="mimir-panel">
              <h4>
                {instance.backend === 'gbrain' ? 'Runtime & storage' : 'Sources & maintenance'}
              </h4>
              <dl>
                {Object.entries(instance.metrics)
                  .filter(([key]) => !['Pages', 'Categories'].includes(key))
                  .map(([key, value]) => (
                    <div className="mimir-detail-row" key={key}>
                      <dt>{key}</dt>
                      <dd>{value}</dd>
                    </div>
                  ))}
              </dl>
            </section>
            <section className="mimir-panel">
              <h4>Results & activity</h4>
              <p>Telemetry reported through the knowledge adapter</p>
              {instance.unavailable.length ? (
                <>
                  <p className="mimir-empty-result">
                    The knowledge adapter does not expose the metrics below. Managed runtime logs
                    and native dream results are shown separately below.
                  </p>
                  <ul>
                    {instance.unavailable.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </>
              ) : (
                <p>No additional result streams are reported.</p>
              )}
            </section>
          </div>
          <DeploymentInspection instanceName={instance.mount} />
          {instance.backend !== 'gbrain' && (
            <RavnsPage key={instance.mount} instanceName={instance.mount} />
          )}
        </div>
      )}
    </section>
  );
}
