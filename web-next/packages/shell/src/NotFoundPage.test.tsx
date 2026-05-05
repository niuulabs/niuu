import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { NotFoundPage } from './NotFoundPage';

describe('NotFoundPage', () => {
  it('renders the 404 heading', () => {
    render(<NotFoundPage />);
    expect(screen.getByRole('heading', { name: '404' })).toBeInTheDocument();
  });

  it('renders the page not found message', () => {
    render(<NotFoundPage />);
    expect(screen.getByText('Page not found.')).toBeInTheDocument();
  });
});
