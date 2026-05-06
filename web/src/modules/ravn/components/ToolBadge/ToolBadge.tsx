import styles from './ToolBadge.module.css';

interface ToolBadgeProps {
  tool: string;
}

export function ToolBadge({ tool }: ToolBadgeProps) {
  return <span className={styles.badge}>{tool}</span>;
}
