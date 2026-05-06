import { useMemo, useRef, useState } from 'react';
import { ShapeSvg, type ShapeColor } from '@niuulabs/ui';
import type { Registry, EntityType } from '../domain';
import type { EntityShape, EntityCategory } from '@niuulabs/domain';
import { isDescendant } from '../domain/containment';
import { useRegistryEditor } from '../application/useRegistryEditor';

// ── Types ─────────────────────────────────────────────────────────────────────

type TabId = 'types' | 'containment' | 'json';

// ── Constants ────────────────────────────────────────────────────────────────

const ALL_SHAPES: EntityShape[] = [
  'dot',
  'ring',
  'ring-dashed',
  'rounded-rect',
  'diamond',
  'triangle',
  'hex',
  'chevron',
  'square',
  'square-sm',
  'pentagon',
  'halo',
  'mimir',
  'mimir-small',
];

const ALL_COLORS: ShapeColor[] = [
  'brand',
  'brand-100',
  'brand-200',
  'brand-300',
  'brand-400',
  'brand-500',
  'ice-100',
  'ice-200',
  'ice-300',
  'slate-300',
  'slate-400',
];

const ALL_CATEGORIES: EntityCategory[] = [
  'topology',
  'hardware',
  'agent',
  'coordinator',
  'knowledge',
  'infrastructure',
  'device',
  'composite',
];

const INPUT_CLS =
  'niuu-w-full niuu-px-3 niuu-py-2 niuu-bg-bg-tertiary niuu-border niuu-border-border niuu-rounded-sm niuu-text-text-primary niuu-text-sm niuu-font-sans niuu-box-border';

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatDate(iso: string) {
  return iso.slice(0, 10);
}

// ── TypePreviewDrawer (editable — matches web2) ─────────────────────────────

interface TypePreviewDrawerProps {
  type: EntityType;
  registry: Registry;
  onClose: () => void;
  onUpdate: (id: string, patch: Partial<EntityType>) => void;
}

function TypePreviewDrawer({ type, registry, onClose, onUpdate }: TypePreviewDrawerProps) {
  const allTypeIds = registry.types.map((t) => t.id).filter((id) => id !== type.id);
  return (
    <div
      data-testid="type-preview-drawer"
      className="niuu-border-l niuu-border-border-subtle niuu-bg-bg-secondary niuu-flex niuu-flex-col niuu-overflow-hidden"
    >
      {/* Header: shape icon + type info */}
      <div className="niuu-p-4 niuu-border-b niuu-border-border-subtle niuu-flex niuu-items-start niuu-gap-3">
        <div className="niuu-w-12 niuu-h-12 niuu-flex niuu-items-center niuu-justify-center niuu-bg-bg-tertiary niuu-rounded-md niuu-border niuu-border-border-subtle niuu-shrink-0">
          <ShapeSvg shape={type.shape} color={type.color as ShapeColor} size={28} />
        </div>
        <div className="niuu-flex-1 niuu-min-w-0">
          <div className="niuu-font-mono niuu-text-[10px] niuu-text-text-muted niuu-uppercase niuu-tracking-[0.07em] niuu-mb-[2px]">
            type · {type.category}
          </div>
          <div className="niuu-text-lg niuu-font-semibold niuu-tracking-[-0.015em] niuu-flex niuu-items-center niuu-gap-2">
            {type.label}
            <span className="niuu-font-mono niuu-text-brand niuu-text-base niuu-font-bold">
              {type.rune}
            </span>
          </div>
          <div className="niuu-font-mono niuu-text-[11px] niuu-text-text-muted niuu-mt-[2px]">
            {type.id}
          </div>
        </div>
        <button
          aria-label="Close preview"
          onClick={onClose}
          className="niuu-bg-transparent niuu-border-0 niuu-cursor-pointer niuu-text-text-muted niuu-p-1 niuu-leading-none niuu-text-base"
        >
          ✕
        </button>
      </div>

      {/* Editable form */}
      <div className="niuu-p-4 niuu-overflow-y-auto niuu-flex-1 niuu-flex niuu-flex-col niuu-gap-4">
        {/* ID */}
        <div>
          <div className="registry-section-head">ID</div>
          <input
            className={INPUT_CLS}
            value={type.id}
            onChange={(e) => onUpdate(type.id, { id: e.target.value })}
            data-testid="edit-id"
          />
        </div>

        {/* Label */}
        <div>
          <div className="registry-section-head">Label</div>
          <input
            className={INPUT_CLS}
            value={type.label}
            onChange={(e) => onUpdate(type.id, { label: e.target.value })}
            data-testid="edit-label"
          />
        </div>

        {/* Category */}
        <div>
          <div className="registry-section-head">Category</div>
          <select
            className={INPUT_CLS}
            value={type.category}
            onChange={(e) => onUpdate(type.id, { category: e.target.value as EntityCategory })}
            data-testid="edit-category"
          >
            {ALL_CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>

        {/* Description */}
        <div>
          <div className="registry-section-head">Description</div>
          <textarea
            className={`${INPUT_CLS} niuu-min-h-[72px] niuu-resize-y`}
            value={type.description}
            onChange={(e) => onUpdate(type.id, { description: e.target.value })}
            data-testid="edit-description"
          />
        </div>

        {/* Visual — Shape picker */}
        <div>
          <div className="registry-section-head">Visual</div>
          <div className="niuu-text-[10px] niuu-uppercase niuu-tracking-[0.07em] niuu-text-text-muted niuu-mb-1">
            Shape
          </div>
          <div className="niuu-grid niuu-grid-cols-7 niuu-gap-1" data-testid="shape-picker">
            {ALL_SHAPES.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => onUpdate(type.id, { shape: s })}
                aria-label={s}
                aria-pressed={type.shape === s}
                className={`niuu-w-9 niuu-h-9 niuu-flex niuu-items-center niuu-justify-center niuu-rounded-sm niuu-border niuu-cursor-pointer ${
                  type.shape === s
                    ? 'niuu-border-brand niuu-bg-bg-elevated'
                    : 'niuu-border-border-subtle niuu-bg-bg-tertiary hover:niuu-border-border'
                }`}
              >
                <ShapeSvg shape={s} color={type.color as ShapeColor} size={16} />
              </button>
            ))}
          </div>
        </div>

        {/* Color picker */}
        <div>
          <div className="niuu-text-[10px] niuu-uppercase niuu-tracking-[0.07em] niuu-text-text-muted niuu-mb-1">
            Color
          </div>
          <div className="niuu-grid niuu-grid-cols-7 niuu-gap-1" data-testid="color-picker">
            {ALL_COLORS.map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => onUpdate(type.id, { color: c })}
                aria-label={c}
                aria-pressed={type.color === c}
                className={`niuu-w-9 niuu-h-9 niuu-rounded-sm niuu-border niuu-cursor-pointer ${
                  type.color === c
                    ? 'niuu-border-brand'
                    : 'niuu-border-border-subtle hover:niuu-border-border'
                }`}
              >
                <ShapeSvg shape="square" color={c} size={24} />
              </button>
            ))}
          </div>
        </div>

        {/* Rune + Size side by side */}
        <div className="niuu-grid niuu-grid-cols-2 niuu-gap-3">
          <div>
            <div className="registry-section-head">Rune</div>
            <input
              className={`${INPUT_CLS} niuu-font-mono niuu-text-center niuu-text-lg`}
              value={type.rune}
              maxLength={2}
              onChange={(e) => onUpdate(type.id, { rune: e.target.value.slice(0, 2) })}
              data-testid="edit-rune"
            />
          </div>
          <div>
            <div className="registry-section-head">Size</div>
            <input
              className={INPUT_CLS}
              type="number"
              value={type.size}
              onChange={(e) => onUpdate(type.id, { size: +e.target.value })}
              data-testid="edit-size"
            />
          </div>
        </div>

        {/* ── Containment ─────────────────────────────────────── */}
        <div className="registry-section-head">Containment</div>

        <div>
          <div className="niuu-text-[10px] niuu-uppercase niuu-tracking-[0.07em] niuu-text-text-muted niuu-mb-1">
            Can contain
          </div>
          <div className="niuu-flex niuu-flex-wrap niuu-gap-1" data-testid="edit-can-contain">
            {allTypeIds.map((id) => {
              const active = type.canContain.includes(id);
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() =>
                    onUpdate(type.id, {
                      canContain: active
                        ? type.canContain.filter((x) => x !== id)
                        : [...type.canContain, id],
                    })
                  }
                  className={`niuu-px-2 niuu-py-[2px] niuu-rounded-sm niuu-border niuu-text-xs niuu-font-mono niuu-cursor-pointer ${
                    active
                      ? 'niuu-border-brand niuu-bg-bg-elevated niuu-text-brand'
                      : 'niuu-border-border-subtle niuu-bg-bg-tertiary niuu-text-text-muted hover:niuu-border-border'
                  }`}
                >
                  {id}
                </button>
              );
            })}
          </div>
        </div>

        <div>
          <div className="niuu-text-[10px] niuu-uppercase niuu-tracking-[0.07em] niuu-text-text-muted niuu-mb-1">
            Can live inside
          </div>
          <div className="niuu-flex niuu-flex-wrap niuu-gap-1" data-testid="edit-parent-types">
            {allTypeIds.map((id) => {
              const active = type.parentTypes.includes(id);
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() =>
                    onUpdate(type.id, {
                      parentTypes: active
                        ? type.parentTypes.filter((x) => x !== id)
                        : [...type.parentTypes, id],
                    })
                  }
                  className={`niuu-px-2 niuu-py-[2px] niuu-rounded-sm niuu-border niuu-text-xs niuu-font-mono niuu-cursor-pointer ${
                    active
                      ? 'niuu-border-brand niuu-bg-bg-elevated niuu-text-brand'
                      : 'niuu-border-border-subtle niuu-bg-bg-tertiary niuu-text-text-muted hover:niuu-border-border'
                  }`}
                >
                  {id}
                </button>
              );
            })}
          </div>
        </div>

        {/* ── Fields (editable) ────────────────────────────────── */}
        <div className="registry-section-head">Fields</div>

        {type.fields.length === 0 && (
          <div className="niuu-text-xs niuu-text-text-muted niuu-font-mono">— no fields —</div>
        )}
        {type.fields.map((f, i) => (
          <div
            key={i}
            className="niuu-grid niuu-gap-[6px] niuu-items-center niuu-mb-[6px]"
            style={{ gridTemplateColumns: '1fr 80px 30px' }}
          >
            <input
              className={`${INPUT_CLS} niuu-h-7 niuu-text-[11px] niuu-py-1 niuu-px-2`}
              value={f.label}
              onChange={(e) => {
                const fields = [...type.fields];
                fields[i] = {
                  ...f,
                  label: e.target.value,
                  key: e.target.value.toLowerCase().replace(/\s+/g, '_'),
                };
                onUpdate(type.id, { fields });
              }}
            />
            <select
              className={`${INPUT_CLS} niuu-h-7 niuu-text-[11px] niuu-py-1 niuu-px-2`}
              value={f.type}
              onChange={(e) => {
                const fields = [...type.fields];
                fields[i] = {
                  ...f,
                  type: e.target.value as 'string' | 'number' | 'boolean' | 'select' | 'tags',
                };
                onUpdate(type.id, { fields });
              }}
            >
              {['string', 'number', 'select', 'tags', 'boolean'].map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="niuu-bg-transparent niuu-border niuu-border-border-subtle niuu-rounded-sm niuu-cursor-pointer niuu-text-text-muted niuu-text-xs niuu-h-7 hover:niuu-text-critical"
              onClick={() => {
                const fields = [...type.fields];
                fields.splice(i, 1);
                onUpdate(type.id, { fields });
              }}
            >
              ×
            </button>
          </div>
        ))}
        <button
          type="button"
          className="niuu-py-1 niuu-px-3 niuu-bg-bg-secondary niuu-text-text-secondary niuu-border niuu-border-border niuu-rounded-sm niuu-cursor-pointer niuu-font-mono niuu-text-xs niuu-mt-1"
          onClick={() =>
            onUpdate(type.id, {
              fields: [...type.fields, { key: 'new_field', label: 'New field', type: 'string' }],
            })
          }
          data-testid="add-field-btn"
        >
          + add field
        </button>
      </div>
    </div>
  );
}

// ── TypesTab ──────────────────────────────────────────────────────────────────

interface TypesTabProps {
  registry: Registry;
  selectedId: string | null;
  onSelect: (id: string) => void;
  search: string;
}

function TypesTab({ registry, selectedId, onSelect, search }: TypesTabProps) {
  const filtered = useMemo(() => {
    if (!search) return registry.types;
    const q = search.toLowerCase();
    return registry.types.filter(
      (t) =>
        t.label.toLowerCase().includes(q) ||
        t.id.includes(q) ||
        t.description.toLowerCase().includes(q),
    );
  }, [registry.types, search]);

  const byCategory = useMemo(() => {
    const m = new Map<string, EntityType[]>();
    for (const t of filtered) {
      if (!m.has(t.category)) m.set(t.category, []);
      m.get(t.category)!.push(t);
    }
    return m;
  }, [filtered]);

  return (
    <div>
      {filtered.length === 0 && (
        <p className="niuu-text-text-muted niuu-text-sm">No types match your search.</p>
      )}

      {[...byCategory.entries()].map(([cat, types]) => (
        <div key={cat} className="niuu-mb-5">
          <div className="niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.07em] niuu-text-text-muted niuu-mb-2 niuu-flex niuu-items-center niuu-gap-2">
            {cat}
            <span className="niuu-font-normal niuu-text-text-muted">· {types.length}</span>
          </div>
          <div className="type-grid">
            {types.map((t) => (
              <button
                key={t.id}
                data-testid={`type-row-${t.id}`}
                data-selected={selectedId === t.id ? 'true' : undefined}
                onClick={() => onSelect(t.id)}
                className="type-card"
              >
                <div className="type-swatch">
                  <ShapeSvg shape={t.shape} color={t.color as ShapeColor} size={22} />
                </div>
                <div className="type-name">
                  {t.label}
                  <span className="niuu-font-mono niuu-text-brand niuu-text-[12px] niuu-font-bold">
                    {t.rune}
                  </span>
                </div>
                <div className="type-meta">
                  <div
                    style={{
                      fontFamily: 'var(--font-mono)',
                      fontSize: 10,
                      color: 'var(--brand-300)',
                    }}
                  >
                    {t.id}
                  </div>
                  <div>
                    shape ·{' '}
                    <strong className="niuu-text-text-secondary niuu-font-medium">{t.shape}</strong>
                  </div>
                </div>
                <div className="type-desc">{t.description.split('.')[0]}.</div>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── ContainmentTab ────────────────────────────────────────────────────────────

type DropState = 'none' | 'ok' | 'target' | 'invalid';

interface ContainmentTabProps {
  registry: Registry;
  selectedId: string | null;
  onSelect: (id: string) => void;
  tryReparent: (childId: string, newParentId: string) => boolean;
}

function ContainmentTab({ registry, selectedId, onSelect, tryReparent }: ContainmentTabProps) {
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);
  // dragIdRef mirrors dragId but updates synchronously — dragover fires immediately
  // after dragstart in headless/CDP environments, before React can flush the state update.
  const dragIdRef = useRef<string | null>(null);

  const byId = useMemo(() => new Map(registry.types.map((t) => [t.id, t])), [registry.types]);

  const roots = useMemo(
    () => registry.types.filter((t) => t.parentTypes.length === 0),
    [registry.types],
  );

  // Build reachable set from roots via canContain edges.
  const reachable = useMemo(() => {
    const set = new Set<string>();
    const mark = (t: EntityType | undefined) => {
      if (!t || set.has(t.id)) return;
      set.add(t.id);
      for (const childId of t.canContain) mark(byId.get(childId));
    };
    for (const root of roots) mark(root);
    return set;
  }, [roots, byId]);

  const orphans = useMemo(
    () => registry.types.filter((t) => !reachable.has(t.id) && t.parentTypes.length > 0),
    [registry.types, reachable],
  );

  const getDropState = (targetId: string): DropState => {
    if (!dragId) return 'none';
    const invalid = isDescendant(registry, dragId, targetId, byId) || dragId === targetId;
    if (overId === targetId) return invalid ? 'invalid' : 'target';
    if (!invalid) return 'ok';
    return 'none';
  };

  const handleDragStart = (e: React.DragEvent, id: string) => {
    dragIdRef.current = id;
    setDragId(id);
    try {
      if (e.dataTransfer) {
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', id);
      }
    } catch {
      // dataTransfer may be unavailable in some contexts (e.g. jsdom)
    }
  };

  const handleDragOver = (e: React.DragEvent, targetId: string) => {
    const currentDragId = dragIdRef.current;
    if (!currentDragId) return;
    const invalid =
      isDescendant(registry, currentDragId, targetId, byId) || currentDragId === targetId;
    try {
      if (e.dataTransfer) e.dataTransfer.dropEffect = invalid ? 'none' : 'move';
    } catch {
      // dataTransfer may be read-only in some contexts
    }
    e.preventDefault();
    setOverId(targetId);
  };

  const handleDragLeave = (e: React.DragEvent, targetId: string) => {
    // Only clear if we're leaving the node itself (not a child element).
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
      setOverId((prev) => (prev === targetId ? null : prev));
    }
  };

  const handleDrop = (e: React.DragEvent, targetId: string) => {
    e.preventDefault();
    const sourceId = dragIdRef.current;
    if (sourceId) tryReparent(sourceId, targetId);
    dragIdRef.current = null;
    setDragId(null);
    setOverId(null);
  };

  const handleDragEnd = () => {
    dragIdRef.current = null;
    setDragId(null);
    setOverId(null);
  };

  const renderNode = (t: EntityType): React.ReactNode => {
    const children = t.canContain.map((id) => byId.get(id)).filter(Boolean) as EntityType[];
    const dropState = getDropState(t.id);
    const isDragging = dragId === t.id;

    return (
      <div key={t.id}>
        <div
          data-testid={`tree-node-${t.id}`}
          data-drag-state={dropState}
          data-selected={selectedId === t.id ? 'true' : undefined}
          data-dragging={isDragging ? 'true' : undefined}
          draggable
          className="registry-tree-node"
          onDragStart={(e) => handleDragStart(e, t.id)}
          onDragOver={(e) => handleDragOver(e, t.id)}
          onDragLeave={(e) => handleDragLeave(e, t.id)}
          onDrop={(e) => handleDrop(e, t.id)}
          onDragEnd={handleDragEnd}
          onClick={() => onSelect(t.id)}
        >
          <span
            aria-hidden
            className="niuu-text-text-muted niuu-font-mono niuu-text-[11px] niuu-select-none"
          >
            ⋮⋮
          </span>
          <span className="niuu-w-4 niuu-inline-flex niuu-justify-center niuu-shrink-0">
            <ShapeSvg shape={t.shape} color={t.color as ShapeColor} size={14} />
          </span>
          <span className="niuu-font-mono niuu-text-brand niuu-text-[13px] niuu-font-bold niuu-shrink-0">
            {t.rune}
          </span>
          <span className="niuu-font-medium niuu-text-sm">{t.label}</span>
          <span className="niuu-font-mono niuu-text-[11px] niuu-text-text-muted niuu-ml-auto">
            {t.id}
          </span>
        </div>
        {children.length > 0 && (
          <div className="registry-tree-children">{children.map((c) => renderNode(c))}</div>
        )}
      </div>
    );
  };

  return (
    <div>
      <p className="containment-hint">
        <strong>Drag</strong> a type onto another to reparent it. The <code>canContain</code> edge
        moves to the new parent; <code>parentTypes</code> on the child updates. Cycles are blocked.
      </p>

      <div data-testid="containment-tree">{roots.map((r) => renderNode(r))}</div>

      {orphans.length > 0 && (
        <div
          data-testid="orphans-section"
          className="niuu-mt-4 niuu-pt-3 niuu-border-t niuu-border-dashed niuu-border-border-subtle"
        >
          <div className="niuu-font-mono niuu-text-[10px] niuu-text-text-muted niuu-uppercase niuu-tracking-[0.08em] niuu-mb-1">
            orphans — parent missing
          </div>
          {orphans.map((o) => renderNode(o))}
        </div>
      )}
    </div>
  );
}

// ── JsonTab ───────────────────────────────────────────────────────────────────

interface JsonTabProps {
  registry: Registry;
}

function JsonTab({ registry }: JsonTabProps) {
  const [copied, setCopied] = useState(false);
  const json = JSON.stringify(registry, null, 2);

  const handleCopy = () => {
    navigator.clipboard.writeText(json).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      },
      () => {
        // clipboard may not be available in test/iframe contexts — ignore
      },
    );
  };

  return (
    <div className="niuu-relative">
      <button
        aria-label="Copy JSON"
        data-testid="copy-json-btn"
        onClick={handleCopy}
        className={`niuu-absolute niuu-top-2 niuu-right-2 niuu-bg-bg-elevated niuu-border niuu-border-border niuu-rounded-sm niuu-cursor-pointer niuu-text-xs niuu-font-mono niuu-px-[10px] niuu-py-1 niuu-z-[1] ${
          copied ? 'niuu-text-brand' : 'niuu-text-text-secondary'
        }`}
      >
        {copied ? 'copied!' : 'copy'}
      </button>
      <pre
        data-testid="json-output"
        className="niuu-m-0 niuu-p-4 niuu-bg-bg-primary niuu-border niuu-border-border-subtle niuu-rounded-md niuu-font-mono niuu-text-[11px] niuu-text-text-secondary niuu-overflow-x-auto niuu-leading-[1.55] niuu-min-h-[480px] niuu-max-h-[calc(100vh-240px)] niuu-overflow-y-auto"
      >
        {json}
      </pre>
    </div>
  );
}

// ── RegistryEditor ────────────────────────────────────────────────────────────

export interface RegistryEditorProps {
  registry: Registry;
}

export function RegistryEditor({ registry: initialRegistry }: RegistryEditorProps) {
  const [activeTab, setActiveTab] = useState<TabId>('types');
  const [search, setSearch] = useState('');
  const { registry, selectedId, select, tryReparent, updateType, createType } =
    useRegistryEditor(initialRegistry);

  const selectedType = registry.types.find((t) => t.id === selectedId) ?? null;
  const showDrawer = selectedType !== null && activeTab !== 'json';

  return (
    <div
      className="niuu-grid niuu-h-full niuu-overflow-hidden niuu-bg-bg-primary"
      style={{ gridTemplateColumns: showDrawer ? '1fr 380px' : '1fr' }}
    >
      {/* Main column */}
      <div className="niuu-flex niuu-flex-col niuu-overflow-hidden">
        {/* Header */}
        <div className="niuu-py-4 niuu-px-6 niuu-border-b niuu-border-border-subtle niuu-shrink-0">
          <div className="niuu-flex niuu-items-baseline niuu-justify-between niuu-mb-1">
            <h2 className="niuu-m-0 niuu-text-xl niuu-font-bold">Entity type registry</h2>
            <span className="niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
              rev <strong className="niuu-text-text-secondary">{registry.version}</strong>
              {' · '}
              {registry.types.length} types
              {' · '}
              updated {formatDate(registry.updatedAt)}
            </span>
          </div>
          <p className="niuu-m-0 niuu-text-text-secondary niuu-text-sm niuu-max-w-[64ch]">
            Every node that appears in the Observatory canvas is an instance of one of these types.
            Edit a type here and the canvas re-renders.
          </p>
        </div>

        {/* Tabs */}
        <div
          role="tablist"
          className="niuu-flex niuu-items-center niuu-border-b niuu-border-border-subtle niuu-px-6 niuu-shrink-0"
        >
          {(['types', 'containment', 'json'] as TabId[]).map((tab) => (
            <button
              key={tab}
              role="tab"
              aria-selected={activeTab === tab}
              data-testid={`tab-${tab}`}
              onClick={() => setActiveTab(tab)}
              className="registry-tab-btn"
            >
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
            </button>
          ))}
          <div className="niuu-flex-1" />
          {activeTab === 'types' && (
            <div className="niuu-flex niuu-items-center niuu-gap-2">
              <input
                aria-label="Filter types"
                placeholder="filter types…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="niuu-h-8 niuu-px-3 niuu-bg-bg-tertiary niuu-border niuu-border-border niuu-rounded-sm niuu-text-text-primary niuu-text-sm niuu-font-sans niuu-box-border"
                style={{ width: 220 }}
              />
              <button
                type="button"
                onClick={createType}
                data-testid="create-type-btn"
                className="niuu-py-1 niuu-px-3 niuu-bg-brand niuu-text-bg-primary niuu-border niuu-border-brand niuu-rounded-sm niuu-cursor-pointer niuu-font-mono niuu-text-xs niuu-whitespace-nowrap"
              >
                + New type
              </button>
            </div>
          )}
        </div>

        {/* Tab content */}
        <div role="tabpanel" className="niuu-flex-1 niuu-overflow-y-auto niuu-py-5 niuu-px-6">
          {activeTab === 'types' && (
            <TypesTab
              registry={registry}
              selectedId={selectedId}
              onSelect={select}
              search={search}
            />
          )}
          {activeTab === 'containment' && (
            <ContainmentTab
              registry={registry}
              selectedId={selectedId}
              onSelect={select}
              tryReparent={tryReparent}
            />
          )}
          {activeTab === 'json' && <JsonTab registry={registry} />}
        </div>
      </div>

      {/* Drawer */}
      {showDrawer && (
        <TypePreviewDrawer
          type={selectedType}
          registry={registry}
          onClose={() => select(null)}
          onUpdate={updateType}
        />
      )}
    </div>
  );
}
