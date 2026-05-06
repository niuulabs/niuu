import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { createMimirMockAdapter } from '../adapters/mock';
import { renderWithMimir as render } from '../testing/renderWithMimir';
import { RegistryPage } from './RegistryPage';

describe('RegistryPage', () => {
  it('renders registered mounts from the service', async () => {
    render(<RegistryPage />, createMimirMockAdapter());
    await waitFor(() => expect(screen.getByText('Registry')).toBeInTheDocument());
    expect(screen.getByText(/\d+ registered · \d+ enabled/i)).toBeInTheDocument();
  });

  it('creates a new registry mount', async () => {
    render(<RegistryPage />, createMimirMockAdapter());
    await waitFor(() => expect(screen.getByText('Add registry mount')).toBeInTheDocument());

    const textboxes = screen.getAllByRole('textbox');
    fireEvent.change(textboxes[0]!, {
      target: { value: 'new-remote' },
    });
    fireEvent.change(textboxes[1]!, {
      target: { value: 'https://mimir.example.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(screen.getByText('new-remote')).toBeInTheDocument());
  });
});
