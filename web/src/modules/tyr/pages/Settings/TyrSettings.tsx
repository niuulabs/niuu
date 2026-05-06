import { useState } from 'react';
import { Plus } from 'lucide-react';
import type { ITyrIntegrationService } from '@/modules/tyr/ports';
import { useTyrIntegrations } from '@/modules/tyr/hooks/useTyrIntegrations';
import { INTEGRATION_TYPES } from '@/modules/tyr/constants';
import { VolundrConnectionSection } from './sections/VolundrConnectionSection';
import { DispatcherSettingsSection } from './sections/DispatcherSettingsSection';
import { FlockSettingsSection } from './sections/FlockSettingsSection';
import styles from './TyrSettings.module.css';

interface TyrSettingsProps {
  service: ITyrIntegrationService;
}

export function TyrSettings({ service }: TyrSettingsProps) {
  const { connections, loading, error, createConnection, deleteConnection } =
    useTyrIntegrations(service);
  const [showAddForm, setShowAddForm] = useState(false);

  const volundrConnections = connections.filter(
    c => c.integrationType === INTEGRATION_TYPES.CODE_FORGE
  );

  if (loading) {
    return (
      <div className={styles.page}>
        <h2 className={styles.heading}>Tyr Connections</h2>
        <p className={styles.loadingText}>Loading...</p>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <h2 className={styles.heading}>Tyr Connections</h2>
          <p className={styles.subheading}>
            Connect Tyr to your Volundr instances for session dispatch
          </p>
        </div>
        <button type="button" className={styles.addButton} onClick={() => setShowAddForm(true)}>
          <Plus className={styles.addIcon} />
          Add Cluster
        </button>
      </div>
      {error && <p className={styles.error}>{error}</p>}
      <VolundrConnectionSection
        connections={volundrConnections}
        onConnect={createConnection}
        onDisconnect={deleteConnection}
        service={service}
        showForm={showAddForm}
        onShowFormChange={setShowAddForm}
      />
      <DispatcherSettingsSection />
      <FlockSettingsSection />
    </div>
  );
}
