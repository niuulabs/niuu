import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('./ui/settings/GeneralSection', () => ({
  GeneralSection: () => <div data-testid="general-section" />,
}));
vi.mock('./ui/settings/DispatchDefaultsSection', () => ({
  DispatchDefaultsSection: () => <div data-testid="dispatch-section" />,
}));
vi.mock('./ui/settings/IntegrationsSection', () => ({
  IntegrationsSection: () => <div data-testid="integrations-section" />,
}));
vi.mock('./ui/settings/PersonasSection', () => ({
  PersonasSection: () => <div data-testid="personas-section" />,
}));
vi.mock('./ui/settings/GatesReviewersSection', () => ({
  GatesReviewersSection: () => <div data-testid="gates-section" />,
}));
vi.mock('./ui/settings/FlockConfigSection', () => ({
  FlockConfigSection: () => <div data-testid="flock-section" />,
}));
vi.mock('./ui/settings/NotificationsSection', () => ({
  NotificationsSection: () => <div data-testid="notifications-section" />,
}));
vi.mock('./ui/settings/AdvancedSection', () => ({
  AdvancedSection: () => <div data-testid="advanced-section" />,
}));
vi.mock('./ui/settings/AuditLogSection', () => ({
  AuditLogSection: () => <div data-testid="audit-section" />,
}));

import { tyrMountedSettingsProvider } from './settingsMount';

describe('tyrMountedSettingsProvider', () => {
  it('describes the Tyr settings mount', () => {
    expect(tyrMountedSettingsProvider).toMatchObject({
      id: 'tyr',
      pluginId: 'tyr',
      title: 'Tyr',
      scope: 'service',
      defaultSectionId: 'general',
    });
    expect(tyrMountedSettingsProvider.sections).toHaveLength(9);
  });

  it('renders each configured settings section', () => {
    for (const section of tyrMountedSettingsProvider.sections) {
      const { unmount } = render(section.render());
      expect(screen.getByTestId(`${section.id}-section`)).toBeInTheDocument();
      unmount();
    }
  });
});
