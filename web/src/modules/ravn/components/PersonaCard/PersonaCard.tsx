import { Link } from 'react-router-dom';
import { cn } from '@/modules/shared/utils/classnames';
import { ToolBadge } from '../ToolBadge';
import type { PersonaSummary } from '../../api/types';
import styles from './PersonaCard.module.css';
import badgeStyles from '../../styles/badges.module.css';

interface PersonaCardProps {
  persona: PersonaSummary;
}

export function PersonaCard({ persona }: PersonaCardProps) {
  const visibleTools = persona.allowedTools.slice(0, 4);
  const extraCount = persona.allowedTools.length - visibleTools.length;

  return (
    <Link
      to={`/ravn/personas/${encodeURIComponent(persona.name)}`}
      className={styles.card}
      data-builtin={persona.isBuiltin}
    >
      <div className={styles.header}>
        <span className={styles.name}>{persona.name}</span>
        <div className={styles.badges}>
          {persona.isBuiltin && (
            <span className={cn(badgeStyles.badge, badgeStyles.builtinBadge)}>built-in</span>
          )}
          {persona.hasOverride && (
            <span className={cn(badgeStyles.badge, badgeStyles.overrideBadge)}>override</span>
          )}
        </div>
      </div>

      <div className={styles.meta}>
        <span className={styles.metaItem}>
          <span className={styles.metaLabel}>mode</span>
          <span className={styles.metaValue}>{persona.permissionMode || '—'}</span>
        </span>
        {persona.iterationBudget > 0 && (
          <span className={styles.metaItem}>
            <span className={styles.metaLabel}>budget</span>
            <span className={styles.metaValue}>{persona.iterationBudget}</span>
          </span>
        )}
        {persona.producesEvent && (
          <span className={styles.metaItem}>
            <span className={styles.metaLabel}>produces</span>
            <span className={styles.metaValue}>{persona.producesEvent}</span>
          </span>
        )}
      </div>

      {visibleTools.length > 0 && (
        <div className={styles.tools}>
          {visibleTools.map(tool => (
            <ToolBadge key={tool} tool={tool} />
          ))}
          {extraCount > 0 && <span className={styles.extraTools}>+{extraCount}</span>}
        </div>
      )}
    </Link>
  );
}
