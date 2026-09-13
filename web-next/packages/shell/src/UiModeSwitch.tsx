import { useEffect, useRef, useState } from 'react';
import {
  useOptionalService,
  type IFeatureCatalogService,
  type PluginDescriptor,
} from '@niuulabs/plugin-sdk';
import { SegmentedFilter } from '@niuulabs/ui';
import {
  cacheUiMode,
  preferencesForMode,
  uiModeFromPreferences,
  useUiMode,
  type UiMode,
} from './uiMode';

const OPTIONS: Array<{ value: UiMode; label: string }> = [
  { value: 'simple', label: 'Simple' },
  { value: 'advanced', label: 'Advanced' },
];

/**
 * Simple / Advanced control in the topbar. The mode flips only once the server has
 * stored the preference; on failure the control shows the error and stays put.
 * A host that wires no `features` service (an embedded consumer) keeps the mode in
 * the browser only; that is the host's decision, not a fallback taken here.
 */
export function UiModeSwitch({ plugins }: { plugins: PluginDescriptor[] }) {
  const features = useOptionalService<IFeatureCatalogService>('features');
  const mode = useUiMode();
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const loaded = useRef(false);

  // Boot: the saved preference wins over the local cache, once.
  useEffect(() => {
    if (loaded.current || !features) return;
    loaded.current = true;
    let cancelled = false;
    features
      .getUserFeaturePreferences()
      .then((preferences) => {
        if (cancelled) return;
        const saved = uiModeFromPreferences(preferences);
        if (saved && saved !== mode) cacheUiMode(saved);
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [features]);

  async function change(next: UiMode) {
    if (next === mode || saving) return;
    setSaving(true);
    setError(null);
    try {
      if (features) await features.updateUserFeaturePreferences(preferencesForMode(next, plugins));
      cacheUiMode(next);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="niuu-shell__mode" data-testid="ui-mode-switch" data-mode={mode}>
      <SegmentedFilter<UiMode>
        options={OPTIONS}
        value={mode}
        onChange={(value) => void change(value)}
        aria-label="Interface mode"
      />
      {error ? (
        <span className="niuu-shell__mode-error" role="alert" title={error}>
          could not save
        </span>
      ) : null}
    </div>
  );
}
