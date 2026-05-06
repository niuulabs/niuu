import { useCallback } from 'react';
import { cn } from '../../utils/cn';
import type { FieldType } from '@niuulabs/domain';
import './SchemaEditor.css';

export type SchemaEditorValue = Record<string, FieldType>;

const FIELD_TYPES: FieldType[] = ['string', 'number', 'boolean', 'object', 'array', 'any'];

export interface SchemaEditorProps {
  value: SchemaEditorValue;
  onChange?: (value: SchemaEditorValue) => void;
  readonly?: boolean;
  className?: string;
}

/**
 * SchemaEditor — key/type grid for editing event payload schemas.
 *
 * Promoted to @niuulabs/ui because Tyr's WorkflowBuilder will need it too.
 * Built here in NIU-673 (Ravn personas page).
 */
export function SchemaEditor({ value, onChange, readonly = false, className }: SchemaEditorProps) {
  const entries = Object.entries(value);

  const handleKeyChange = useCallback(
    (oldKey: string, newKey: string) => {
      if (!onChange) return;
      const next: SchemaEditorValue = {};
      for (const [k, v] of Object.entries(value)) {
        next[k === oldKey ? newKey : k] = v;
      }
      onChange(next);
    },
    [value, onChange],
  );

  const handleTypeChange = useCallback(
    (key: string, type: FieldType) => {
      if (!onChange) return;
      onChange({ ...value, [key]: type });
    },
    [value, onChange],
  );

  const handleAddRow = useCallback(() => {
    if (!onChange) return;
    let name = 'field';
    let i = 1;
    while (Object.hasOwn(value, name)) {
      name = `field${i++}`;
    }
    onChange({ ...value, [name]: 'string' });
  }, [value, onChange]);

  const handleRemoveRow = useCallback(
    (key: string) => {
      if (!onChange) return;
      const next = { ...value };
      delete next[key];
      onChange(next);
    },
    [value, onChange],
  );

  return (
    <div
      className={cn('niuu-schema-editor', readonly && 'niuu-schema-editor--readonly', className)}
    >
      <table className="niuu-schema-editor__table" aria-label="Event payload schema">
        <thead>
          <tr>
            <th className="niuu-schema-editor__th niuu-schema-editor__th--key">Field</th>
            <th className="niuu-schema-editor__th niuu-schema-editor__th--type">Type</th>
            {!readonly && <th className="niuu-schema-editor__th niuu-schema-editor__th--action" />}
          </tr>
        </thead>
        <tbody>
          {entries.map(([key, type]) => (
            <tr key={key} className="niuu-schema-editor__row">
              <td className="niuu-schema-editor__td niuu-schema-editor__td--key">
                {readonly ? (
                  <span className="niuu-schema-editor__key-label">{key}</span>
                ) : (
                  <input
                    className="niuu-schema-editor__key-input niuu-form-control"
                    value={key}
                    onChange={(e) => handleKeyChange(key, e.target.value)}
                    aria-label="Field name"
                  />
                )}
              </td>
              <td className="niuu-schema-editor__td niuu-schema-editor__td--type">
                {readonly ? (
                  <span className="niuu-schema-editor__type-badge">{type}</span>
                ) : (
                  <select
                    className="niuu-form-control niuu-schema-editor__type-select"
                    value={type}
                    onChange={(e) => handleTypeChange(key, e.target.value as FieldType)}
                    aria-label="Field type"
                  >
                    {FIELD_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                )}
              </td>
              {!readonly && (
                <td className="niuu-schema-editor__td niuu-schema-editor__td--action">
                  <button
                    type="button"
                    onClick={() => handleRemoveRow(key)}
                    className="niuu-schema-editor__remove-btn"
                    aria-label={`Remove field ${key}`}
                  >
                    ✕
                  </button>
                </td>
              )}
            </tr>
          ))}
          {entries.length === 0 && (
            <tr>
              <td colSpan={readonly ? 2 : 3} className="niuu-schema-editor__empty">
                {readonly ? 'No payload fields' : 'No fields — click Add field to start'}
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {!readonly && (
        <button type="button" onClick={handleAddRow} className="niuu-schema-editor__add-btn">
          + Add field
        </button>
      )}
    </div>
  );
}
