import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ServicesProvider, useService } from './ServicesProvider';

interface FakeService {
  say(): string;
}

function Reader() {
  const svc = useService<FakeService>('fake');
  return <span data-testid="out">{svc.say()}</span>;
}

describe('ServicesProvider', () => {
  it('exposes injected services via useService', () => {
    const fake: FakeService = { say: () => 'hi' };
    render(
      <ServicesProvider services={{ fake }}>
        <Reader />
      </ServicesProvider>,
    );
    expect(screen.getByTestId('out').textContent).toBe('hi');
  });

  it('throws when useService is called outside the provider', () => {
    const error = console.error;
    console.error = () => {};
    expect(() => render(<Reader />)).toThrow(/ServicesProvider/);
    console.error = error;
  });

  it('throws when the requested key is not registered', () => {
    const error = console.error;
    console.error = () => {};
    expect(() =>
      render(
        <ServicesProvider services={{}}>
          <Reader />
        </ServicesProvider>,
      ),
    ).toThrow(/not registered/);
    console.error = error;
  });
});
