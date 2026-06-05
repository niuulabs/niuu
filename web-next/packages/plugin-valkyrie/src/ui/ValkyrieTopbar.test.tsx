import { describe, expect, it } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { ValkyrieTopbar } from './ValkyrieTopbar';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';

describe('ValkyrieTopbar', () => {
  it('renders live resident counters', async () => {
    render(<ValkyrieTopbar />, { wrapper: wrapWithValkyrie() });

    expect(await screen.findByTestId('valkyrie-topbar')).toBeInTheDocument();
    await waitFor(() =>
      expect(within(screen.getByTestId('valkyrie-topbar')).getAllByText('1')).toHaveLength(2),
    );
  });
});
