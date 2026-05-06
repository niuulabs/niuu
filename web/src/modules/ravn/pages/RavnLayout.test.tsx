import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { RavnLayout } from './RavnLayout';

function wrap(initialPath = '/ravn/chat') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/ravn" element={<RavnLayout />}>
          <Route path="chat" element={<div>Chat Content</div>} />
          <Route path="sessions" element={<div>Sessions Content</div>} />
          <Route path="personas" element={<div>Personas Content</div>} />
          <Route path="config" element={<div>Config Content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>
  );
}

describe('RavnLayout', () => {
  it('renders the brand name', () => {
    wrap();
    expect(screen.getByText('Ravn')).toBeInTheDocument();
  });

  it('renders all nav links', () => {
    wrap();
    expect(screen.getByRole('link', { name: 'Chat' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Sessions' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Personas' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Config' })).toBeInTheDocument();
  });

  it('renders outlet content', () => {
    wrap('/ravn/chat');
    expect(screen.getByText('Chat Content')).toBeInTheDocument();
  });

  it('Chat nav link points to correct path', () => {
    wrap();
    expect(screen.getByRole('link', { name: 'Chat' })).toHaveAttribute('href', '/ravn/chat');
  });

  it('Personas nav link points to correct path', () => {
    wrap();
    expect(screen.getByRole('link', { name: 'Personas' })).toHaveAttribute(
      'href',
      '/ravn/personas'
    );
  });

  it('Sessions nav link points to correct path', () => {
    wrap();
    expect(screen.getByRole('link', { name: 'Sessions' })).toHaveAttribute(
      'href',
      '/ravn/sessions'
    );
  });

  it('Config nav link points to correct path', () => {
    wrap();
    expect(screen.getByRole('link', { name: 'Config' })).toHaveAttribute('href', '/ravn/config');
  });
});
