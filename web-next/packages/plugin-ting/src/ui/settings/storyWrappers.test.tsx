import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { buildWrapper } from './storyWrappers';

describe('buildWrapper', () => {
  it('renders wrapped children', () => {
    const Wrapper = buildWrapper({ alpha: { ok: true } });
    render(
      <Wrapper>
        <div data-testid="wrapped-child">wrapped</div>
      </Wrapper>,
    );

    expect(screen.getByTestId('wrapped-child')).toHaveTextContent('wrapped');
  });
});
