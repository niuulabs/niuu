import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { GeneralSection } from './GeneralSection';

describe('GeneralSection', () => {
  it('renders the section heading', () => {
    render(<GeneralSection />);
    expect(screen.getByText('General')).toBeInTheDocument();
  });

  it('renders the description', () => {
    render(<GeneralSection />);
    expect(screen.getByText('Core service bindings for the saga coordinator.')).toBeInTheDocument();
  });

  it('renders all 4 KV rows', () => {
    render(<GeneralSection />);
    expect(screen.getByText('Service URL')).toBeInTheDocument();
    expect(screen.getByText('https://tyr.niuu.internal')).toBeInTheDocument();
    expect(screen.getByText('Event backbone')).toBeInTheDocument();
    expect(screen.getByText('sleipnir · nats')).toBeInTheDocument();
    expect(screen.getByText('Knowledge store')).toBeInTheDocument();
    expect(screen.getByText('mímir · qdrant:/niuu')).toBeInTheDocument();
    expect(screen.getByText('Default workflow')).toBeInTheDocument();
    expect(screen.getByText('tpl-ship v1.4.2')).toBeInTheDocument();
  });

  it('has an accessible section label', () => {
    render(<GeneralSection />);
    expect(screen.getByRole('region', { name: /general settings/i })).toBeInTheDocument();
  });

  it('renders a list of service bindings', () => {
    render(<GeneralSection />);
    expect(screen.getByRole('list', { name: /service bindings/i })).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(4);
  });
});
