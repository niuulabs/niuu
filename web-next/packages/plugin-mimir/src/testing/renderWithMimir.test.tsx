import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { usePluginCtx, useService } from '@niuulabs/plugin-sdk';
import { renderWithMimir } from './renderWithMimir';

describe('renderWithMimir helpers', () => {
  it('provides plugin context and services to rendered children', () => {
    function Probe() {
      const pluginCtx = usePluginCtx();
      const mimir = useService('mimir');
      return (
        <div>
          <span data-testid="tweak">{pluginCtx.tweaks['activeMount']}</span>
          <span data-testid="has-mimir">{String(Boolean(mimir))}</span>
        </div>
      );
    }

    renderWithMimir(<Probe />, undefined, { tweaks: { activeMount: 'shared' } });
    expect(screen.getByTestId('tweak')).toHaveTextContent('shared');
    expect(screen.getByTestId('has-mimir')).toHaveTextContent('true');
  });
});
