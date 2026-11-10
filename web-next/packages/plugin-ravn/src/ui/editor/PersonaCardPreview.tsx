import {
  Bot,
  Code2,
  FlaskConical,
  ListChecks,
  PenLine,
  ScanSearch,
  ShieldCheck,
} from 'lucide-react';
import type { PersonaDocument } from '../../ports';
import {
  documentDescription,
  documentName,
  outcomesOf,
  startsOn,
} from '../../application/personaDocument';

/** The icon the workflow builder gives a step run by this persona. */
function PersonaIcon({ name }: { name: string }) {
  const id = name.toLowerCase();
  const props = { size: 17, strokeWidth: 1.8, 'aria-hidden': true } as const;
  if (id.includes('secur') || id.includes('audit')) return <ShieldCheck {...props} />;
  if (id.includes('review')) return <ScanSearch {...props} />;
  if (id.includes('test') || id.includes('qa')) return <FlaskConical {...props} />;
  if (id.includes('plan')) return <ListChecks {...props} />;
  if (id.includes('doc') || id.includes('writ')) return <PenLine {...props} />;
  if (id.includes('code') || id.includes('merge') || id.includes('build')) {
    return <Code2 {...props} />;
  }
  return <Bot {...props} />;
}

/**
 * The persona's card as the workflow builder draws a step it runs: its name and purpose, the
 * events that start it on the left, one connector per outcome on the right.
 */
export function PersonaCardPreview({ doc }: { doc: PersonaDocument }) {
  const name = documentName(doc).trim();
  const inputs = startsOn(doc);
  const outputs = outcomesOf(doc).map((outcome) => outcome.verdict);
  const rows = Math.max(inputs.length, outputs.length, 1);

  return (
    <figure className="rw-ed-card" data-testid="persona-card-preview">
      <div className="rw-ed-card__box">
        <div className="rw-ed-card__head">
          <span className="rw-ed-card__tile">
            <PersonaIcon name={name} />
          </span>
          <span className="rw-ed-card__text">
            <span className="rw-ed-card__title">{name || 'new-persona'}</span>
            <span className="rw-ed-card__sub">{documentDescription(doc) || 'What it is for'}</span>
          </span>
        </div>
        <div className="rw-ed-card__ports">
          {Array.from({ length: rows }, (_, index) => (
            <div className="rw-ed-card__row" key={index}>
              <span className="rw-ed-card__in">
                {inputs[index] && (
                  <>
                    <i className="rw-ed-card__dot" data-direction="input" />
                    <span className="rw-ed-card__label">{inputs[index]}</span>
                  </>
                )}
              </span>
              <span className="rw-ed-card__out">
                {outputs[index] && (
                  <>
                    <span className="rw-ed-card__label">{outputs[index]}</span>
                    <i className="rw-ed-card__dot" data-direction="output" />
                  </>
                )}
              </span>
            </div>
          ))}
        </div>
      </div>
      <figcaption className="rw-ed-card__legend">
        <span>Starts on these</span>
        <span>Wire each outcome on</span>
      </figcaption>
    </figure>
  );
}
