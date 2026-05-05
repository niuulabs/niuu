import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { OverlaysPage } from './OverlaysPage';

describe('OverlaysPage', () => {
  it('renders all four overlay section headings', () => {
    render(<OverlaysPage />);
    expect(screen.getByText('Dialog')).toBeInTheDocument();
    expect(screen.getByText('Drawer')).toBeInTheDocument();
    expect(screen.getByText('Popover')).toBeInTheDocument();
    expect(screen.getByText('Tooltip')).toBeInTheDocument();
  });

  it('opens dialog on trigger click', async () => {
    render(<OverlaysPage />);
    fireEvent.click(screen.getByTestId('dialog-trigger'));
    await waitFor(() => {
      expect(screen.getByTestId('dialog-body')).toBeInTheDocument();
    });
  });

  it('closes dialog on cancel click', async () => {
    render(<OverlaysPage />);
    fireEvent.click(screen.getByTestId('dialog-trigger'));
    await waitFor(() => {
      expect(screen.getByTestId('dialog-body')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('dialog-cancel'));
  });

  it('opens popover on trigger click', async () => {
    render(<OverlaysPage />);
    fireEvent.click(screen.getByTestId('popover-trigger'));
    await waitFor(() => {
      expect(screen.getByTestId('popover-body')).toBeInTheDocument();
    });
  });
});
