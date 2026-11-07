import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TerminalDock } from './TerminalDock';

describe('TerminalDock', () => {
  it('holds the terminal at its height under its top edge', () => {
    render(
      <TerminalDock height={230} edge={<div role="separator" aria-label="edge" />}>
        <p>shell</p>
      </TerminalDock>,
    );
    const dock = screen.getByRole('region', { name: 'Terminal' });
    expect(dock).toHaveStyle({ height: '230px' });
    expect(dock).toHaveTextContent('shell');
    expect(screen.getByRole('separator', { name: 'edge' })).toBeInTheDocument();
  });
});
