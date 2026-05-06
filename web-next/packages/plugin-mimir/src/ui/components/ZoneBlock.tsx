import { useRef } from 'react';
import { StateDot } from '@niuulabs/ui';
import { MountChip } from './MountChip';
import { ZoneBodyReadonly, zoneToEditableText } from './ZoneRenderers';
import type { ZoneEditState } from '../../domain/zone-edit';
import type { Zone } from '../../domain/page';
import type { PageMeta } from '../../domain/page';

const ZONE_LABELS: Record<string, string> = {
  'key-facts': 'Key facts',
  relationships: 'Relationships',
  assessment: 'Assessment',
  timeline: 'Timeline',
};

interface ZoneBlockProps {
  zone: Zone;
  pagePath: string;
  pageMounts: string[];
  allPages: PageMeta[];
  onNavigate: (path: string) => void;
  editState: ZoneEditState;
  onEdit: (zone: Zone) => void;
  onSave: (text: string) => void;
  onCancel: () => void;
}

function EditZoneBody({
  zone,
  textareaRef,
}: {
  zone: Zone;
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
}) {
  const text = zoneToEditableText(zone);
  return (
    <div className="niuu-flex niuu-flex-col niuu-gap-2 niuu-pt-2">
      <textarea
        ref={textareaRef}
        className="niuu-w-full niuu-min-h-[120px] niuu-p-3 niuu-bg-bg-primary niuu-border niuu-border-border niuu-rounded-sm niuu-text-text-primary niuu-text-sm niuu-font-mono niuu-resize-y"
        defaultValue={text}
        aria-label="zone edit area"
      />
      <p className="niuu-text-xs niuu-text-text-muted niuu-m-0">
        Editing {zone.kind} zone — changes will be written to the destination mount.
      </p>
    </div>
  );
}

export function ZoneBlock({
  zone,
  pagePath,
  pageMounts,
  allPages,
  onNavigate,
  editState,
  onEdit,
  onSave,
  onCancel,
}: ZoneBlockProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const isEditingThis =
    editState.status === 'editing' &&
    editState.path === pagePath &&
    editState.zoneKind === zone.kind;
  const isSavingThis = editState.status === 'saving' && editState.path === pagePath;
  const isSaved = editState.status === 'saved' && editState.path === pagePath;
  const errorMessage =
    editState.status === 'error' && editState.path === pagePath ? editState.message : null;

  const canEdit = editState.status === 'idle';
  // Timeline zones are append-only — editing is disabled by design.
  const isTimeline = zone.kind === 'timeline';
  const label = ZONE_LABELS[zone.kind] ?? zone.kind;

  return (
    <div className="niuu-mb-6 niuu-border niuu-border-border-subtle niuu-rounded-lg niuu-overflow-hidden">
      <div
        className={[
          'niuu-flex niuu-items-center niuu-justify-between niuu-px-4 niuu-py-3 niuu-border-b niuu-border-border-subtle',
          isEditingThis ? 'mm-zone-head--editing' : 'niuu-bg-bg-secondary',
        ].join(' ')}
      >
        <span className="niuu-text-xs niuu-uppercase niuu-tracking-[0.07em] niuu-text-text-muted">
          {label}
        </span>
        <div className="niuu-flex niuu-items-center niuu-gap-2">
          {isSavingThis && <StateDot state="processing" pulse />}
          {canEdit && !isEditingThis && !isTimeline && (
            <button
              type="button"
              className="mm-btn"
              onClick={() => onEdit(zone)}
              aria-label={`edit ${zone.kind} zone`}
            >
              ✎ edit
            </button>
          )}
          {isEditingThis && (
            <>
              <button
                type="button"
                className="mm-btn mm-btn--primary"
                onClick={() => onSave(textareaRef.current?.value ?? '')}
                aria-label={`save ${zone.kind} zone`}
              >
                save
              </button>
              <button type="button" className="mm-btn" onClick={onCancel} aria-label="cancel edit">
                cancel
              </button>
            </>
          )}
        </div>
      </div>

      <div className="niuu-p-4">
        {isSaved && (
          <div className="mm-save-banner">
            ✓ saved →{' '}
            {pageMounts.map((m) => (
              <MountChip key={m} name={m} />
            ))}
          </div>
        )}
        {errorMessage && <div className="mm-error-banner">{errorMessage}</div>}

        {isEditingThis ? (
          <EditZoneBody zone={editState.draft} textareaRef={textareaRef} />
        ) : (
          <ZoneBodyReadonly zone={zone} allPages={allPages} onNavigate={onNavigate} />
        )}
      </div>
    </div>
  );
}
