import { useState } from 'react';
import { Toggle } from '@/modules/shared';
import { useFlockConfig } from '@/modules/tyr/hooks/useFlockConfig';
import styles from './FlockSettingsSection.module.css';

const LLM_PRESETS: Record<string, Record<string, unknown>> = {
  'local-vllm': {
    model: 'Qwen/Qwen2.5-Coder-32B-Instruct',
    provider: 'openai',
    base_url: 'http://vllm:8000/v1',
  },
  'anthropic-sonnet': {
    model: 'claude-sonnet-4-6',
    provider: 'anthropic',
  },
  'anthropic-opus': {
    model: 'claude-opus-4-6',
    provider: 'anthropic',
  },
};

export function FlockSettingsSection() {
  const {
    config,
    loading,
    updating,
    error,
    setFlockEnabled,
    setDefaultPersonas,
    setLlmConfig,
    setSleipnirUrls,
  } = useFlockConfig();

  const [localEnabled, setLocalEnabled] = useState<boolean | null>(null);
  const [localPersonas, setLocalPersonas] = useState<string | null>(null);
  const [localLlmPreset, setLocalLlmPreset] = useState('');
  const [localLlmJson, setLocalLlmJson] = useState<string | null>(null);
  const [localUrls, setLocalUrls] = useState<string | null>(null);
  const [jsonError, setJsonError] = useState<string | null>(null);

  if (loading) {
    return (
      <section className={styles.section}>
        <h3 className={styles.sectionTitle}>Flock Dispatch</h3>
        <p className={styles.loadingText}>Loading flock settings…</p>
      </section>
    );
  }

  const flockEnabled = localEnabled ?? config?.flock_enabled ?? false;
  const personasText =
    localPersonas ?? config?.flock_default_personas.map(p => p.name).join(', ') ?? '';
  const urlsText = localUrls ?? config?.flock_sleipnir_publish_urls.join('\n') ?? '';
  const llmJson = localLlmJson ?? JSON.stringify(config?.flock_llm_config ?? {}, null, 2);

  const handleToggle = async () => {
    const next = !flockEnabled;
    await setFlockEnabled(next);
    setLocalEnabled(next);
  };

  const handlePersonasSave = async () => {
    const names = personasText
      .split(',')
      .map(s => s.trim())
      .filter(Boolean);
    await setDefaultPersonas(names);
  };

  const handlePresetChange = (preset: string) => {
    setLocalLlmPreset(preset);
    if (preset && LLM_PRESETS[preset]) {
      setLocalLlmJson(JSON.stringify(LLM_PRESETS[preset], null, 2));
      setJsonError(null);
    }
  };

  const handleLlmJsonChange = (value: string) => {
    setLocalLlmJson(value);
    setLocalLlmPreset('');
    try {
      JSON.parse(value);
      setJsonError(null);
    } catch {
      setJsonError('Invalid JSON');
    }
  };

  const handleLlmSave = async () => {
    if (jsonError) return;
    try {
      const config = JSON.parse(llmJson);
      await setLlmConfig(config);
    } catch {
      setJsonError('Invalid JSON');
    }
  };

  const handleUrlsSave = async () => {
    const urls = urlsText
      .split('\n')
      .map(s => s.trim())
      .filter(Boolean);
    await setSleipnirUrls(urls);
  };

  return (
    <section className={styles.section}>
      <h3 className={styles.sectionTitle}>Flock Dispatch</h3>
      <p className={styles.sectionDescription}>
        Enable multi-agent flock sessions. When enabled, raids are dispatched as ravn_flock sessions
        with multiple collaborating personas.
      </p>

      {error && <p className={styles.errorText}>{error}</p>}

      {/* Enabled toggle */}
      <div className={styles.settingRow}>
        <div className={styles.settingInfo}>
          <span className={styles.settingLabel}>Flock enabled</span>
          <span className={styles.settingDescription}>
            Gate all flock UI and dispatch behaviour
          </span>
        </div>
        <Toggle
          checked={flockEnabled}
          onChange={() => void handleToggle()}
          label="Flock enabled"
          disabled={updating}
        />
      </div>

      {flockEnabled && (
        <>
          {/* Default personas */}
          <div className={styles.settingBlock}>
            <label className={styles.blockLabel}>Default personas</label>
            <p className={styles.blockDescription}>
              Comma-separated persona names for every flock session.
            </p>
            <div className={styles.inputRow}>
              <input
                className={styles.textInput}
                value={personasText}
                onChange={e => setLocalPersonas(e.target.value)}
                placeholder="coordinator, reviewer, security-auditor"
              />
              <button
                type="button"
                className={styles.saveButton}
                onClick={() => void handlePersonasSave()}
                disabled={updating}
              >
                Save
              </button>
            </div>
          </div>

          {/* LLM config */}
          <div className={styles.settingBlock}>
            <label className={styles.blockLabel}>LLM config</label>
            <p className={styles.blockDescription}>Provider and model for ravn sidecar nodes.</p>
            <div className={styles.presetRow}>
              <select
                className={styles.select}
                value={localLlmPreset}
                onChange={e => handlePresetChange(e.target.value)}
              >
                <option value="">Custom…</option>
                <option value="local-vllm">Local vLLM (Qwen)</option>
                <option value="anthropic-sonnet">Anthropic (Claude Sonnet)</option>
                <option value="anthropic-opus">Anthropic (Claude Opus)</option>
              </select>
            </div>
            <textarea
              className={styles.jsonTextarea}
              value={llmJson}
              onChange={e => handleLlmJsonChange(e.target.value)}
              rows={6}
              spellCheck={false}
            />
            {jsonError && <p className={styles.errorText}>{jsonError}</p>}
            <button
              type="button"
              className={styles.saveButton}
              onClick={() => void handleLlmSave()}
              disabled={updating || !!jsonError}
            >
              Save LLM config
            </button>
          </div>

          {/* Sleipnir publish URLs */}
          <div className={styles.settingBlock}>
            <label className={styles.blockLabel}>Sleipnir publish URLs</label>
            <p className={styles.blockDescription}>
              One URL per line. Event routing for flock task lifecycle.
            </p>
            <textarea
              className={styles.urlsTextarea}
              value={urlsText}
              onChange={e => setLocalUrls(e.target.value)}
              rows={3}
              placeholder="http://sleipnir:4222"
            />
            <button
              type="button"
              className={styles.saveButton}
              onClick={() => void handleUrlsSave()}
              disabled={updating}
            >
              Save URLs
            </button>
          </div>
        </>
      )}
    </section>
  );
}
