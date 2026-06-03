import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ValkyrieSubnav } from './ValkyrieSubnav';

describe('ValkyrieSubnav', () => {
  it('renders Valkyrie navigation labels', () => {
    render(<ValkyrieSubnav />);

    expect(screen.getByTestId('valkyrie-subnav')).toBeInTheDocument();
    expect(screen.getByText('Environments')).toBeInTheDocument();
    expect(screen.getByText('Flocks')).toBeInTheDocument();
  });
});
