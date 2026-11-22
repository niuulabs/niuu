import './LaunchWizard.css';
import type { McpServerConfig } from '../models/volundr.model';

/** On/off choices for the MCP servers a host's operator offers its sessions. */
export function SessionToolOptions({
  servers,
  selected,
  onChange,
}: {
  servers: readonly McpServerConfig[];
  selected: readonly string[];
  onChange: (selected: string[]) => void;
}) {
  return (
    <>
      {servers.map((server) => {
        const checked = selected.includes(server.name);
        const detail = [server.description, server.version].filter(Boolean).join(' · ');
        return (
          <label
            key={server.name}
            className={`vol-launch-wizard__access-option vol-launch-wizard__access-option--stacked ${
              checked ? 'vol-launch-wizard__access-option--checked' : ''
            }`}
          >
            <input
              type="checkbox"
              className="vol-launch-wizard__access-option-input"
              checked={checked}
              onChange={(event) =>
                onChange(
                  event.target.checked
                    ? [...selected, server.name]
                    : selected.filter((name) => name !== server.name),
                )
              }
              aria-label={server.name}
            />
            <span className="vol-launch-wizard__access-option-box" aria-hidden="true">
              {checked ? '✓' : ''}
            </span>
            <span className="niuu:flex niuu:flex-col">
              <span>{server.name}</span>
              {detail ? (
                <span className="niuu:text-[11px] niuu:text-text-faint">{detail}</span>
              ) : null}
            </span>
          </label>
        );
      })}
    </>
  );
}
