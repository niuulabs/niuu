import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { createSeedValkyrieDashboard } from '../adapters/mock';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { ValkyrieConsolePage } from './ValkyrieConsolePage';

describe('ValkyrieConsolePage', () => {
  it('shows the loading state first', () => {
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    expect(screen.getByTestId('valkyrie-console-loading')).toBeInTheDocument();
  });

  it('renders the rich console with roster, situation, timeline, and authority panels', async () => {
    const seed = createSeedValkyrieDashboard();
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });

    expect(await screen.findByTestId('valkyrie-console-page')).toBeInTheDocument();
    expect(screen.getByTestId('valkyrie-roster')).toHaveTextContent(seed.valkyries[0]!.name);
    expect(screen.getByTestId('valkyrie-console-hero')).toHaveTextContent(
      `valkyrie:${seed.valkyries[0]!.name}`,
    );
    expect(screen.getByTestId('valkyrie-current-situation')).toBeInTheDocument();
    expect(screen.getByTestId('valkyrie-signal-timeline')).toBeInTheDocument();
    expect(screen.getByTestId('valkyrie-authority')).toHaveTextContent('Authority & autonomy');
  });

  it('surfaces dashboard failures', async () => {
    const broken = {
      getDashboard: () => Promise.reject(new Error('dashboard offline')),
      updateAutonomy: () => Promise.reject(new Error('nope')),
    };
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie({ valkyrie: broken }) });
    expect(await screen.findByTestId('valkyrie-console-error')).toHaveTextContent(
      'dashboard offline',
    );
  });
});
