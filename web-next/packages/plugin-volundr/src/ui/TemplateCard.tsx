import { Chip } from '@niuulabs/ui';
import type { Template } from '../domain/template';
import { maskSecretRefs } from '../application/templateUtils';
import './TemplateCard.css';

export interface TemplateCardProps {
  template: Template;
  onEdit: (template: Template) => void;
  onClone: (template: Template) => void;
  isCloning?: boolean;
}

export function TemplateCard({ template, onEdit, onClone, isCloning = false }: TemplateCardProps) {
  const { spec } = template;
  const maskedEnv = maskSecretRefs(spec.env, spec.envSecretRefs);
  const secretOnlyEntries = spec.envSecretRefs
    .filter((key) => !(key in spec.env))
    .map((key): [string, string] => [key, '***']);
  const envEntries = [...Object.entries(maskedEnv), ...secretOnlyEntries];

  return (
    <article className="tpl-card" data-testid="template-card">
      <div className="tpl-card__header">
        <div className="tpl-card__title-row">
          <span className="tpl-card__name">{template.name}</span>
          <span className="tpl-card__version" aria-label={`version ${template.version}`}>
            v{template.version}
          </span>
        </div>
        <div className="tpl-card__image">
          <span className="tpl-card__image-text">
            {spec.image}:{spec.tag}
          </span>
        </div>
      </div>

      <div className="tpl-card__resources">
        <Chip tone="default">
          CPU {spec.resources.cpuRequest}–{spec.resources.cpuLimit}
        </Chip>
        <Chip tone="default">
          Mem {spec.resources.memRequestMi}–{spec.resources.memLimitMi} Mi
        </Chip>
        {spec.resources.gpuCount > 0 && <Chip tone="brand">GPU ×{spec.resources.gpuCount}</Chip>}
      </div>

      <div className="tpl-card__timing">
        <span className="tpl-card__timing-item">
          TTL <strong>{Math.round(spec.ttlSec / 60)}m</strong>
        </span>
        <span className="tpl-card__timing-sep" aria-hidden="true">
          ·
        </span>
        <span className="tpl-card__timing-item">
          Idle <strong>{Math.round(spec.idleTimeoutSec / 60)}m</strong>
        </span>
      </div>

      {spec.tools.length > 0 && (
        <div className="tpl-card__tools">
          {spec.tools.map((tool) => (
            <Chip key={tool} tone="muted">
              {tool}
            </Chip>
          ))}
        </div>
      )}

      {envEntries.length > 0 && (
        <dl className="tpl-card__env" aria-label="environment variables">
          {envEntries.map(([key, val]) => (
            <div key={key} className="tpl-card__env-row">
              <dt className="tpl-card__env-key">{key}</dt>
              <dd className="tpl-card__env-val">{val}</dd>
            </div>
          ))}
        </dl>
      )}

      {spec.clusterAffinity && spec.clusterAffinity.length > 0 && (
        <div className="tpl-card__affinity">
          <span className="tpl-card__affinity-label">Affinity:</span>
          {spec.clusterAffinity.map((c) => (
            <Chip key={c} tone="muted">
              {c}
            </Chip>
          ))}
        </div>
      )}

      <div className="tpl-card__actions">
        <button
          className="tpl-card__btn tpl-card__btn--secondary"
          onClick={() => onClone(template)}
          disabled={isCloning}
          aria-label={`Clone template ${template.name}`}
        >
          {isCloning ? 'Cloning…' : 'Clone'}
        </button>
        <button
          className="tpl-card__btn tpl-card__btn--primary"
          onClick={() => onEdit(template)}
          aria-label={`Edit template ${template.name}`}
        >
          Edit
        </button>
      </div>

      <div className="tpl-card__meta">
        Updated {new Date(template.updatedAt).toLocaleDateString()}
      </div>
    </article>
  );
}
