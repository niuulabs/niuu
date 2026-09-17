import { beforeEach, describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ForgeSessionSettings } from './ForgeSessionSettings';

describe('session display settings', () => {
  beforeEach(() => localStorage.clear());
  it('keeps Chat required, defaults Diff and Files on, and persists optional tabs', () => {
    const { unmount } = render(<ForgeSessionSettings />);
    expect(screen.getByRole('checkbox', { name: /Chat/ })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /Chat/ })).toBeDisabled();
    for (const name of ['Diff', 'Files'])
      expect(screen.getByRole('checkbox', { name })).toBeChecked();
    for (const name of ['Terminal', 'Chronicle', 'Telemetry', 'Log']) {
      expect(screen.getByRole('checkbox', { name })).not.toBeChecked();
    }
    fireEvent.click(screen.getByRole('checkbox', { name: 'Terminal' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Files' }));
    unmount();
    render(<ForgeSessionSettings />);
    expect(screen.getByRole('checkbox', { name: 'Terminal' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Files' })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: /Chat/ })).toBeChecked();
  });
});
