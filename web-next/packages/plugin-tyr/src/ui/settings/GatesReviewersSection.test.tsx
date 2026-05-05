import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { GatesReviewersSection } from './GatesReviewersSection';

describe('GatesReviewersSection', () => {
  it('renders the section heading', () => {
    render(<GatesReviewersSection />);
    expect(screen.getByText('Gates & reviewers')).toBeInTheDocument();
  });

  it('renders the description', () => {
    render(<GatesReviewersSection />);
    expect(
      screen.getByText('Who can approve gates in workflows. Routing rules.'),
    ).toBeInTheDocument();
  });

  it('renders all 3 reviewers', () => {
    render(<GatesReviewersSection />);
    expect(screen.getByText('jonas@niuulabs.io')).toBeInTheDocument();
    expect(screen.getByText('oskar@niuulabs.io')).toBeInTheDocument();
    expect(screen.getByText('yngve@niuulabs.io')).toBeInTheDocument();
  });

  it('shows routing rules for each reviewer', () => {
    render(<GatesReviewersSection />);
    const routingTexts = screen.getAllByText('all gates · auto-forward after 30m');
    expect(routingTexts).toHaveLength(3);
  });

  it('has an accessible section label', () => {
    render(<GatesReviewersSection />);
    expect(screen.getByRole('region', { name: /gates and reviewers/i })).toBeInTheDocument();
  });

  it('renders an accessible reviewer list', () => {
    render(<GatesReviewersSection />);
    expect(screen.getByRole('list', { name: /reviewer list/i })).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(3);
  });
});
