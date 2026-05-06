import styles from './Toggle.module.css';

export interface ToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  disabled?: boolean;
  accent?: 'brand' | 'purple';
}

export function Toggle({ checked, onChange, label, disabled, accent = 'brand' }: ToggleProps) {
  return (
    <button
      type="button"
      className={styles.toggle}
      role="switch"
      aria-label={label}
      aria-checked={checked}
      data-accent={accent}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span className={styles.toggleThumb} />
    </button>
  );
}
