import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { extractLinks, LinkedText } from './LinkedText';

describe('LinkedText', () => {
  it('turns in-app paths and URLs into links and keeps the rest as text', () => {
    render(
      <LinkedText text="No slot is free. Raise the limit in Setup → Runtime & access (/setup?step=runtime). Docs: https://docs.example.com/docker-mode." />,
    );
    const path = screen.getByRole('link', { name: '/setup?step=runtime' });
    expect(path).toHaveAttribute('href', '/setup?step=runtime');
    const url = screen.getByRole('link', { name: 'https://docs.example.com/docker-mode' });
    expect(url).toHaveAttribute('href', 'https://docs.example.com/docker-mode');
    expect(screen.getByText(/No slot is free\./)).toBeInTheDocument();
  });

  it('leaves plain text and paths inside words alone', () => {
    const { container } = render(<LinkedText text="Ratio 3/4 and a/b are not links" />);
    expect(container.querySelectorAll('a')).toHaveLength(0);
    expect(extractLinks('Ratio 3/4 and a/b')).toEqual([]);
  });

  it('lists the links a message carries', () => {
    expect(extractLinks('see /settings/integrations, then /setup?step=runtime.')).toEqual([
      '/settings/integrations',
      '/setup?step=runtime',
    ]);
  });
});
