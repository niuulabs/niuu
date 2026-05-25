import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { TingSubnav } from './TingSubnav';

describe('TingSubnav', () => {
  it('renders no visible content', () => {
    const view = render(<TingSubnav />);
    expect(view.container).toBeEmptyDOMElement();
  });
});
