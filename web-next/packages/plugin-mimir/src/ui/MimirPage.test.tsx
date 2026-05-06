import { describe, it, expect } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { MimirPage } from './MimirPage';
import { createMimirMockAdapter } from '../adapters/mock';
import type { IMimirService } from '../ports';
import { renderWithMimir } from '../testing/renderWithMimir';

const wrap = renderWithMimir;

describe('MimirPage', () => {
  it('renders the overview view by default', () => {
    wrap(<MimirPage />);
    // MimirPage delegates title/tabs to the shell; it renders the view directly
    expect(screen.getByText(/loading/)).toBeInTheDocument();
  });

  it('renders the pages view when defaultTab is pages', () => {
    wrap(<MimirPage defaultTab="pages" />);
    expect(screen.getByRole('complementary', { name: /page tree/ })).toBeInTheDocument();
  });

  it('renders the sources view when defaultTab is sources', () => {
    wrap(<MimirPage defaultTab="sources" />);
    expect(screen.getByText(/sources/i)).toBeInTheDocument();
  });

  it('renders KPI metrics on the Overview tab after data loads', async () => {
    wrap(<MimirPage />);
    await waitFor(() =>
      expect(screen.getByRole('group', { name: 'KPI metrics' })).toBeInTheDocument(),
    );
  });

  it('renders mount names on the Overview tab', async () => {
    wrap(<MimirPage />);
    await waitFor(() => expect(screen.getAllByText('local').length).toBeGreaterThan(0));
    expect(screen.getAllByText('shared').length).toBeGreaterThan(0);
  });

  it('shows error message on overview when mounts fail', async () => {
    const failing: IMimirService = {
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        listMounts: async () => {
          throw new Error('mount service unavailable');
        },
      },
    };
    wrap(<MimirPage />, failing);
    await waitFor(() => expect(screen.getByText('mount service unavailable')).toBeInTheDocument());
  });
});
