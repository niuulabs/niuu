import { useState } from 'react';
import { Chip } from '@niuulabs/ui';
import { useRouting } from '../application/useRouting';
import { useMimirSources } from './useMimirSources';

const INPUT_CLS =
  'niuu-w-full niuu-py-2 niuu-px-3 niuu-bg-bg-secondary niuu-border niuu-border-solid niuu-border-border ' +
  'niuu-rounded-md niuu-text-text-primary niuu-font-sans niuu-text-sm niuu-outline-none niuu-box-border ' +
  'focus:niuu-border-brand';

const TEXTAREA_CLS = `${INPUT_CLS} niuu-resize-y niuu-min-h-[8rem] niuu-font-sans`;

const LABEL_CLS =
  'niuu-text-[10px] niuu-uppercase niuu-tracking-wider niuu-text-text-muted niuu-block niuu-mb-1';

const SECTION_HEAD = 'niuu-flex niuu-items-baseline niuu-justify-between niuu-mb-3';

const H3_CLS = 'niuu-text-base niuu-font-semibold niuu-m-0';

const META_CLS = 'niuu-font-mono niuu-text-[11px] niuu-text-text-muted';

export function IngestPage() {
  const [title, setTitle] = useState('Niuu SDD §5 — dispatch protocol');
  const [path, setPath] = useState('projects/niuu/dispatch.md');
  const [content, setContent] = useState(
    'The dispatch protocol specifies how Týr hands a saga off to a raid…',
  );

  const { rules, isLoading: rulesLoading } = useRouting();
  const { data: sources = [] } = useMimirSources();

  // Resolve which rule matches the current path
  const resolved = (() => {
    const activeRules = rules.filter((r) => r.active).sort((a, b) => a.priority - b.priority);
    for (const rule of activeRules) {
      if (path.startsWith(rule.prefix)) {
        return { matched: rule.prefix, mount: rule.mountName };
      }
    }
    return { matched: null, mount: 'local' };
  })();

  return (
    <div className="niuu-p-6 niuu-overflow-y-auto niuu-h-full" data-testid="ingest-page">
      <div className="niuu-grid niuu-grid-cols-1 lg:niuu-grid-cols-2 niuu-gap-8">
        {/* ── Left column: Ingest a source ────────────────────── */}
        <div>
          <div className={SECTION_HEAD}>
            <h3 className={H3_CLS}>Ingest a source</h3>
            <span className={META_CLS}>raw → compiled truth</span>
          </div>

          <div className="niuu-flex niuu-flex-col niuu-gap-3">
            <div>
              <label className={LABEL_CLS} htmlFor="ingest-title">
                Source title
              </label>
              <input
                id="ingest-title"
                className={INPUT_CLS}
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </div>

            <div>
              <label className={LABEL_CLS} htmlFor="ingest-path">
                Target page path
              </label>
              <input
                id="ingest-path"
                className={INPUT_CLS}
                type="text"
                value={path}
                onChange={(e) => setPath(e.target.value)}
              />
              <div className="niuu-font-mono niuu-text-[10px] niuu-text-text-muted niuu-mt-1">
                the path tells write-routing which mount(s) this goes to
              </div>
            </div>

            <div>
              <label className={LABEL_CLS} htmlFor="ingest-content">
                Raw content
              </label>
              <textarea
                id="ingest-content"
                className={TEXTAREA_CLS}
                value={content}
                onChange={(e) => setContent(e.target.value)}
                rows={8}
              />
            </div>

            <div className="niuu-flex niuu-items-center niuu-gap-2">
              <button
                type="button"
                className={
                  'niuu-py-1.5 niuu-px-3 niuu-bg-brand niuu-border niuu-border-solid niuu-border-brand ' +
                  'niuu-rounded-md niuu-text-bg-primary niuu-font-sans niuu-text-xs niuu-font-medium niuu-cursor-pointer niuu-whitespace-nowrap'
                }
                aria-label="Ingest source"
              >
                Ingest
              </button>
              <button
                type="button"
                className={
                  'niuu-py-1.5 niuu-px-3 niuu-bg-bg-secondary niuu-border niuu-border-solid niuu-border-border ' +
                  'niuu-rounded-md niuu-text-text-primary niuu-font-sans niuu-text-xs niuu-cursor-pointer niuu-whitespace-nowrap'
                }
                aria-label="Fetch URL"
              >
                Fetch URL…
              </button>
              <button
                type="button"
                className={
                  'niuu-py-1.5 niuu-px-3 niuu-bg-transparent niuu-border niuu-border-solid niuu-border-border-subtle ' +
                  'niuu-rounded-md niuu-text-text-secondary niuu-font-sans niuu-text-xs niuu-cursor-pointer niuu-whitespace-nowrap'
                }
                aria-label="Upload file"
              >
                Upload file…
              </button>
              <span className="niuu-flex-1" />
              <span className="niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
                Skögul (ingest-scout) will tag categories
              </span>
            </div>
          </div>
        </div>

        {/* ── Right column: Write routing + Recent sources ────── */}
        <div>
          <div className={SECTION_HEAD}>
            <h3 className={H3_CLS}>Write routing</h3>
            <span className={META_CLS}>path prefix → target mounts</span>
          </div>

          {rulesLoading ? (
            <div className="niuu-text-sm niuu-text-text-muted">loading rules…</div>
          ) : (
            <>
              <div
                className="niuu-flex niuu-flex-col niuu-gap-[2px] niuu-bg-bg-secondary niuu-rounded-md niuu-p-2"
                data-testid="routing-rules"
              >
                {rules
                  .filter((r) => r.active)
                  .sort((a, b) => a.priority - b.priority)
                  .map((rule) => {
                    const isHit = resolved.matched === rule.prefix;
                    return (
                      <div
                        key={rule.id}
                        className={[
                          'niuu-flex niuu-items-center niuu-justify-between niuu-py-1 niuu-px-2 niuu-rounded-sm',
                          isHit
                            ? 'niuu-bg-[color-mix(in_srgb,var(--status-emerald)_12%,transparent)]'
                            : '',
                        ].join(' ')}
                        data-testid="routing-rule-row"
                      >
                        <span className="niuu-font-mono niuu-text-xs niuu-text-text-primary">
                          {isHit ? '▸ ' : '  '}
                          {rule.prefix}
                        </span>
                        <Chip tone={isHit ? 'brand' : 'muted'}>{rule.mountName}</Chip>
                      </div>
                    );
                  })}
                <div className="niuu-flex niuu-items-center niuu-justify-between niuu-py-1 niuu-px-2 niuu-text-text-muted niuu-italic">
                  <span className="niuu-font-mono niuu-text-xs">{'  '}default</span>
                  <Chip tone="muted">local</Chip>
                </div>
              </div>

              {/* Resolved route box */}
              <div
                className={
                  'niuu-mt-4 niuu-p-3 niuu-rounded-md niuu-font-mono niuu-text-[11px] niuu-leading-relaxed ' +
                  'niuu-bg-[color-mix(in_srgb,var(--brand-300)_6%,transparent)] ' +
                  'niuu-border niuu-border-solid niuu-border-[color-mix(in_srgb,var(--brand-300)_25%,transparent)]'
                }
                data-testid="resolved-route"
              >
                <div className="niuu-text-text-muted niuu-uppercase niuu-tracking-wider niuu-text-[10px] niuu-mb-1">
                  Resolved for <span className="niuu-text-brand-300">{path || '(empty)'}</span>
                </div>
                {resolved.matched ? (
                  <span>
                    matched prefix <span className="niuu-text-brand-300">{resolved.matched}</span> →
                    writes to{' '}
                    <span className="niuu-text-[var(--status-emerald)]">{resolved.mount}</span>
                  </span>
                ) : (
                  <span>no prefix match — falls through to default ({resolved.mount})</span>
                )}
              </div>
            </>
          )}

          {/* Recent sources */}
          <div className={`${SECTION_HEAD} niuu-mt-6`}>
            <h3 className={H3_CLS}>Recent sources</h3>
            <span className={META_CLS}>{sources.length} · across all mounts</span>
          </div>

          <div className="niuu-flex niuu-flex-col niuu-gap-1">
            {sources.slice(0, 10).map((s) => (
              <div
                key={s.id}
                className={
                  'niuu-flex niuu-items-center niuu-gap-3 niuu-py-[6px] niuu-px-3 ' +
                  'niuu-bg-bg-secondary niuu-rounded-sm hover:niuu-bg-bg-tertiary'
                }
                data-testid="recent-source-row"
              >
                <span className="niuu-font-mono niuu-text-[10px] niuu-text-text-muted niuu-shrink-0">
                  {s.id.slice(0, 10)}
                </span>
                <span className="niuu-text-sm niuu-text-text-primary niuu-truncate niuu-flex-1">
                  {s.title}
                </span>
                <span className="niuu-text-[10px] niuu-text-text-faint niuu-shrink-0">
                  {s.ingestAgent}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
