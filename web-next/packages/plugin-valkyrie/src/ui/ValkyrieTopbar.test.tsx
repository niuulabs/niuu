import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { ValkyrieTopbar } from './ValkyrieTopbar';

describe('ValkyrieTopbar', () => {
  it('shows the pending review count and active residents', async () => {
    render(<ValkyrieTopbar />, { wrapper: wrapWithValkyrie() });

    await waitFor(() => {
      expect(screen.getByTestId('topbar-pending')).toHaveTextContent('3');
    });
    expect(screen.getByTestId('valkyrie-topbar')).toBeInTheDocument();
  });
});
