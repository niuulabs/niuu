import { Link } from '@tanstack/react-router';
import { Copy } from 'lucide-react';
import { REALM_TEMPLATES } from '../domain/templates';
import { TemplateIcon } from './icons';
import { SentenceComposer } from './SentenceComposer';

const CHOICE =
  'niuu:flex niuu:flex-col niuu:gap-3 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-5';
const OPTION =
  'niuu:flex niuu:items-center niuu:gap-3 niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-3.5 niuu:py-3 niuu:text-left niuu:text-sm niuu:text-text-primary niuu:hover:border-brand/50';

/**
 * The first thing a newcomer sees: three ways to start a realm and nothing else.
 * Everything a realm produces lives one click further, once there is one.
 */
export function StarterHome({
  realmCount,
  onClone,
  onSeeRealms,
}: {
  realmCount: number;
  onClone: () => void;
  onSeeRealms: () => void;
}) {
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-8" data-testid="starter-home">
      <div className="niuu:flex niuu:max-w-2xl niuu:flex-col niuu:gap-2">
        <span className="niuu:font-mono niuu:text-[11px] niuu:uppercase niuu:tracking-[0.3em] niuu:text-brand-300">
          start here
        </span>
        <h1 className="niuu:m-0 niuu:text-3xl niuu:font-bold niuu:tracking-tight niuu:text-text-primary">
          Give a resident something to keep.
        </h1>
        <p className="niuu:m-0 niuu:text-[15px] niuu:text-text-secondary">
          A resident reads your tracker, works the tickets in sessions, checks quality and watches
          health. You review what it asks you to. Pick one way to begin.
        </p>
      </div>

      <div className="niuu:grid niuu:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] niuu:gap-5">
        <section className={CHOICE} data-testid="starter-sentence">
          <div className="niuu:flex niuu:flex-col niuu:gap-1">
            <span className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
              1 · Say it in one sentence
            </span>
            <span className="niuu:text-xs niuu:text-text-muted">
              Name the repository and the board, say what to ask you about. The resident fills in
              the rest and asks for what it cannot infer.
            </span>
          </div>
          <SentenceComposer />
        </section>

        <section className={CHOICE} data-testid="starter-templates">
          <div className="niuu:flex niuu:flex-col niuu:gap-1">
            <span className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
              2 · Pick a template
            </span>
            <span className="niuu:text-xs niuu:text-text-muted">
              Four kinds of resident, each with sensible trust. You fill in the blanks.
            </span>
          </div>
          <div className="niuu:flex niuu:flex-col niuu:gap-2">
            {REALM_TEMPLATES.map((template) => (
              <Link
                key={template.id}
                to="/realms/new"
                search={{ template: template.id } as never}
                className={OPTION}
                data-testid={`starter-template-${template.id}`}
              >
                <span className="niuu:flex niuu:h-7 niuu:w-7 niuu:shrink-0 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-brand/40 niuu:bg-brand/10 niuu:text-brand">
                  <TemplateIcon templateId={template.id} size={14} />
                </span>
                <span className="niuu:flex niuu:min-w-0 niuu:flex-col">
                  <span className="niuu:font-medium">{template.name}</span>
                  <span className="niuu:truncate niuu:text-[11px] niuu:text-text-muted">
                    {template.keepsDoing[0]}
                  </span>
                </span>
              </Link>
            ))}
          </div>
        </section>
      </div>

      {realmCount > 0 ? (
        <section className={`${CHOICE} niuu:max-w-2xl`} data-testid="starter-clone">
          <div className="niuu:flex niuu:items-center niuu:gap-3">
            <span className="niuu:flex niuu:h-7 niuu:w-7 niuu:shrink-0 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-brand/40 niuu:bg-brand/10 niuu:text-brand">
              <Copy size={14} aria-hidden="true" />
            </span>
            <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
              <span className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
                3 · Clone one that already works
              </span>
              <span className="niuu:text-xs niuu:text-text-muted">
                Same charter, trust and template; only the repository, board and name change.
              </span>
            </div>
            <button type="button" className={OPTION} onClick={onClone}>
              Clone a realm
            </button>
          </div>
        </section>
      ) : null}

      <button
        type="button"
        className="niuu:self-start niuu:text-xs niuu:text-brand-300"
        onClick={onSeeRealms}
        data-testid="starter-see-realms"
      >
        {realmCount > 0
          ? `Skip this and show the ${realmCount} realm${realmCount === 1 ? '' : 's'} that exist ›`
          : 'Show the realms view ›'}
      </button>
    </div>
  );
}
