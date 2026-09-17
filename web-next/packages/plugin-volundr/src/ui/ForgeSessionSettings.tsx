import { useForgePreference } from './useForgePreference';
import './ForgeSessionSettings.css';

export const SESSION_TAB_SETTINGS = [
  { id: 'chat', label: 'Chat', always: true },
  { id: 'terminal', label: 'Terminal' },
  { id: 'diffs', label: 'Diff' },
  { id: 'files', label: 'Files' },
  { id: 'chronicles', label: 'Chronicle' },
  { id: 'telemetry', label: 'Telemetry' },
  { id: 'logs', label: 'Log' },
] as const;

export function useSessionTabs() {
  const [value, setValue] = useForgePreference<string>('sessionTabs', 'chat,diffs,files');
  const enabled = new Set(value.split(','));
  enabled.add('chat');
  const setEnabled = (id: string, checked: boolean) => {
    if (id === 'chat') return;
    if (checked) enabled.add(id);
    else enabled.delete(id);
    setValue(
      SESSION_TAB_SETTINGS.filter((tab) => enabled.has(tab.id))
        .map((tab) => tab.id)
        .join(','),
    );
  };
  return { enabled, setEnabled };
}

export function ForgeSessionSettings() {
  const { enabled, setEnabled } = useSessionTabs();
  return (
    <fieldset className="forge-session-settings">
      <legend>Session tabs</legend>
      <p>
        Choose which tabs appear above each session. Changes are saved automatically in this
        browser.
      </p>
      <div className="forge-session-settings-options">
        {SESSION_TAB_SETTINGS.map((tab) => (
          <label key={tab.id}>
            <input
              type="checkbox"
              checked={enabled.has(tab.id)}
              disabled={tab.id === 'chat'}
              onChange={(event) => setEnabled(tab.id, event.target.checked)}
            />
            <span>{tab.label}</span>
            {tab.id === 'chat' && <small>Always shown</small>}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
