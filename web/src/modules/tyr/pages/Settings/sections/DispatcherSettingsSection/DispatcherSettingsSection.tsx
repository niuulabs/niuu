import { Toggle } from '@/modules/shared';
import { useDispatcher } from '@/modules/tyr/hooks/useDispatcher';
import styles from './DispatcherSettingsSection.module.css';

export function DispatcherSettingsSection() {
  const { state, loading, error, setAutoContinue } = useDispatcher();

  if (loading) {
    return (
      <section className={styles.section}>
        <h3 className={styles.sectionTitle}>Dispatcher</h3>
        <p className={styles.loadingText}>Loading dispatcher settings…</p>
      </section>
    );
  }

  if (error) {
    return (
      <section className={styles.section}>
        <h3 className={styles.sectionTitle}>Dispatcher</h3>
        <p className={styles.errorText}>{error}</p>
      </section>
    );
  }

  if (!state) {
    return null;
  }

  const handleToggle = (next: boolean) => {
    void setAutoContinue(next);
  };

  return (
    <section className={styles.section}>
      <h3 className={styles.sectionTitle}>Dispatcher</h3>
      <div className={styles.settingRow}>
        <div className={styles.settingInfo}>
          <span className={styles.settingLabel}>Auto-continue</span>
          <span className={styles.settingDescription}>
            Automatically dispatch newly unblocked raids after merge
          </span>
        </div>
        <Toggle checked={state.auto_continue} onChange={handleToggle} label="Auto-continue" />
      </div>
    </section>
  );
}
