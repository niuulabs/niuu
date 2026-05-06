import { useMemo, useState } from 'react';
import type { Registry, EntityType } from '../domain';
import type { EntityShape, EntityCategory } from '@niuulabs/domain';
import { isDescendant, reparent } from '../domain/containment';

export interface RegistryEditorState {
  registry: Registry;
  selectedId: string | null;
  select: (id: string | null) => void;
  /**
   * Attempts to reparent `childId` under `newParentId`.
   * Returns `false` (and makes no change) if the drop would create a cycle or
   * if childId === newParentId.
   */
  tryReparent: (childId: string, newParentId: string) => boolean;
  /** Update a field on the selected entity type. */
  updateType: (id: string, patch: Partial<EntityType>) => void;
  /** Create a new entity type and select it. */
  createType: () => void;
}

export function useRegistryEditor(initial: Registry): RegistryEditorState {
  const [registry, setRegistry] = useState<Registry>(initial);
  const [selectedId, setSelectedId] = useState<string | null>(initial.types[0]?.id ?? null);

  const byId = useMemo(() => new Map(registry.types.map((t) => [t.id, t])), [registry.types]);

  const tryReparent = (childId: string, newParentId: string): boolean => {
    if (childId === newParentId) return false;
    if (isDescendant(registry, childId, newParentId, byId)) return false;
    setRegistry((r) => reparent(r, childId, newParentId));
    return true;
  };

  const updateType = (id: string, patch: Partial<EntityType>) => {
    setRegistry((r) => ({
      ...r,
      version: r.version + 1,
      updatedAt: new Date().toISOString(),
      types: r.types.map((t) => (t.id === id ? { ...t, ...patch } : t)),
    }));
  };

  const createType = () => {
    const newId = `new-type-${Date.now()}`;
    const newType: EntityType = {
      id: newId,
      label: 'New Type',
      category: 'topology' as EntityCategory,
      rune: '◇',
      shape: 'ring' as EntityShape,
      color: 'brand',
      description: '',
      parentTypes: [],
      canContain: [],
      icon: '',
      size: 18,
      border: 'solid',
      fields: [],
    };
    setRegistry((r) => ({
      ...r,
      version: r.version + 1,
      updatedAt: new Date().toISOString(),
      types: [...r.types, newType],
    }));
    setSelectedId(newId);
  };

  return {
    registry,
    selectedId,
    select: setSelectedId,
    tryReparent,
    updateType,
    createType,
  };
}
