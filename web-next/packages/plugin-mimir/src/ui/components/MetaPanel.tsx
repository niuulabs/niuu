import { PageTypeGlyph } from './PageTypeGlyph';
import { MountChip } from './MountChip';
import type { Page } from '../../domain/page';
import type { PageMeta } from '../../domain/page';

interface MetaPanelProps {
  page: Page;
  sources: { id: string; title: string; originType: string }[];
  allPages: PageMeta[];
  onNavigate: (path: string) => void;
}

export function MetaPanel({ page, sources, allPages, onNavigate }: MetaPanelProps) {
  const backlinks = allPages.filter((p) => p.related?.some((slug) => page.path.includes(slug)));

  return (
    <div className="mm-rightpanel">
      <div className="mm-meta-head">
        <h4>Page detail</h4>
      </div>

      <div className="mm-meta-block">
        <h5>Provenance</h5>
        <div className="mm-meta-row">
          <span className="mm-meta-k">path</span>
          <span className="mm-meta-v">{page.path}</span>
        </div>
        <div className="mm-meta-row">
          <span className="mm-meta-k">type</span>
          <span className="mm-meta-v">
            <PageTypeGlyph type={page.type} showLabel />
          </span>
        </div>
        <div className="mm-meta-row">
          <span className="mm-meta-k">confidence</span>
          <span className="mm-meta-v">{page.confidence}</span>
        </div>
        <div className="mm-meta-row">
          <span className="mm-meta-k">updated</span>
          <span className="mm-meta-v">{page.updatedAt.slice(0, 10)}</span>
        </div>
        <div className="mm-meta-row">
          <span className="mm-meta-k">by</span>
          <span className="mm-meta-v" style={{ color: 'var(--brand-300)', cursor: 'pointer' }}>
            {page.updatedBy}
          </span>
        </div>
      </div>

      <div className="mm-meta-block">
        <h5>Lives on</h5>
        <div className="mm-mount-chips">
          {page.mounts.map((m) => (
            <MountChip key={m} name={m} />
          ))}
        </div>
      </div>

      {sources.length > 0 && (
        <div className="mm-meta-block">
          <h5>Sources ({sources.length})</h5>
          {sources.map((s) => (
            <div key={s.id} className="mm-source-pill">
              <span className="mm-source-pill__id">{s.id.slice(0, 8)}</span>
              <span className="mm-source-pill__title" title={s.title}>
                {s.title}
              </span>
            </div>
          ))}
        </div>
      )}

      {backlinks.length > 0 && (
        <div className="mm-meta-block">
          <h5>Backlinks ({backlinks.length})</h5>
          {backlinks.slice(0, 6).map((p) => (
            <button
              key={p.path}
              type="button"
              className="mm-backlink"
              onClick={() => onNavigate(p.path)}
            >
              ↩ {p.title}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
