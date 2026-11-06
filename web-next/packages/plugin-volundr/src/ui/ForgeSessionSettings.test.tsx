import { beforeEach, describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ForgeSessionSettings } from './ForgeSessionSettings';

describe('session display settings', () => {
  beforeEach(() => localStorage.clear());
  it('keeps Chat required, defaults Diff, Files and Telemetry on, and persists optional tabs', () => {
    const { unmount } = render(<ForgeSessionSettings />);
    expect(screen.getByRole('checkbox', { name: /Chat/ })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /Chat/ })).toBeDisabled();
    for (const name of ['Diff', 'Files', 'Telemetry'])
      expect(screen.getByRole('checkbox', { name })).toBeChecked();
    for (const name of ['Chronicle', 'Log']) {
      expect(screen.getByRole('checkbox', { name })).not.toBeChecked();
    }
    // The terminal docks under the conversation instead of being a tab.
    expect(screen.queryByRole('checkbox', { name: 'Terminal' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Chronicle' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Files' }));
    unmount();
    render(<ForgeSessionSettings />);
    expect(screen.getByRole('checkbox', { name: 'Chronicle' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Files' })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: /Chat/ })).toBeChecked();
  });
});
