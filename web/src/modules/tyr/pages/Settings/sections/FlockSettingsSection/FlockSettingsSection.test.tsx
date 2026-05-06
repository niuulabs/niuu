import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { FlockSettingsSection } from './FlockSettingsSection';

vi.mock('@/modules/tyr/hooks/useFlockConfig', () => ({
  useFlockConfig: vi.fn(),
}));

import { useFlockConfig } from '@/modules/tyr/hooks/useFlockConfig';

const mockFlockConfig = {
  config: {
    flock_enabled: false,
    flock_default_personas: [
      { name: 'coordinator', llm: {} },
      { name: 'reviewer', llm: {} },
    ],
    flock_llm_config: {},
    flock_sleipnir_publish_urls: [],
  },
  loading: false,
  updating: false,
  error: null,
  setFlockEnabled: vi.fn().mockResolvedValue(undefined),
  setDefaultPersonas: vi.fn().mockResolvedValue(undefined),
  setLlmConfig: vi.fn().mockResolvedValue(undefined),
  setSleipnirUrls: vi.fn().mockResolvedValue(undefined),
};

describe('FlockSettingsSection', () => {
  beforeEach(() => {
    vi.mocked(useFlockConfig).mockReturnValue(mockFlockConfig);
  });

  it('renders section title', () => {
    render(<FlockSettingsSection />);
    expect(screen.getByText('Flock Dispatch')).toBeInTheDocument();
  });

  it('renders loading state', () => {
    vi.mocked(useFlockConfig).mockReturnValue({
      ...mockFlockConfig,
      loading: true,
    });
    render(<FlockSettingsSection />);
    expect(screen.getByText(/loading flock settings/i)).toBeInTheDocument();
  });

  it('renders flock enabled toggle', () => {
    render(<FlockSettingsSection />);
    expect(screen.getByRole('switch')).toBeInTheDocument();
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
  });

  it('hides sub-settings when flock disabled', () => {
    render(<FlockSettingsSection />);
    expect(screen.queryByText('Default personas')).not.toBeInTheDocument();
  });

  it('shows sub-settings when flock enabled', () => {
    vi.mocked(useFlockConfig).mockReturnValue({
      ...mockFlockConfig,
      config: { ...mockFlockConfig.config, flock_enabled: true },
    });
    render(<FlockSettingsSection />);
    expect(screen.getByText('Default personas')).toBeInTheDocument();
    expect(screen.getByText('LLM config')).toBeInTheDocument();
    expect(screen.getByText('Sleipnir publish URLs')).toBeInTheDocument();
  });

  it('calls setFlockEnabled on toggle click', () => {
    render(<FlockSettingsSection />);
    fireEvent.click(screen.getByRole('switch'));
    expect(mockFlockConfig.setFlockEnabled).toHaveBeenCalledWith(true);
  });

  it('shows error when present', () => {
    vi.mocked(useFlockConfig).mockReturnValue({
      ...mockFlockConfig,
      error: 'Update failed',
    });
    render(<FlockSettingsSection />);
    expect(screen.getByText('Update failed')).toBeInTheDocument();
  });

  it('shows preset selector when flock enabled', () => {
    vi.mocked(useFlockConfig).mockReturnValue({
      ...mockFlockConfig,
      config: { ...mockFlockConfig.config, flock_enabled: true },
    });
    render(<FlockSettingsSection />);
    expect(screen.getByText('Local vLLM (Qwen)')).toBeInTheDocument();
    expect(screen.getByText('Anthropic (Claude Sonnet)')).toBeInTheDocument();
  });
});
