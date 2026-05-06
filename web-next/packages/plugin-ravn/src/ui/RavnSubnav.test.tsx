import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import { RavnSubnav } from './RavnSubnav';
import {
  createMockBudgetStream,
  createMockPersonaStore,
  createMockRavenStream,
  createMockSessionStream,
  createMockTriggerStore,
} from '../adapters/mock';
import { wrapWithServices } from '../testing/wrapWithRavn';

const mockPathname = vi.fn(() => '/ravn');

vi.mock('@tanstack/react-router', () => ({
  useRouterState: ({ select }: { select: (s: { location: { pathname: string } }) => unknown }) =>
    select({ location: { pathname: mockPathname() } }),
}));

function services() {
  return {
    'ravn.personas': createMockPersonaStore(),
    'ravn.ravens': createMockRavenStream(),
    'ravn.sessions': createMockSessionStream(),
    'ravn.triggers': createMockTriggerStore(),
    'ravn.budget': createMockBudgetStream(),
  };
}

beforeEach(() => {
  mockPathname.mockReturnValue('/ravn');
});

describe('RavnSubnav', () => {
  it('renders null for /ravn', () => {
    const { container } = render(<RavnSubnav />, { wrapper: wrapWithServices(services()) });
    expect(container.firstChild).toBeNull();
  });

  it('renders null for /ravn/ravens', () => {
    mockPathname.mockReturnValue('/ravn/ravens');
    const { container } = render(<RavnSubnav />, { wrapper: wrapWithServices(services()) });
    expect(container.firstChild).toBeNull();
  });

  it('renders null for /ravn/personas', () => {
    mockPathname.mockReturnValue('/ravn/personas');
    const { container } = render(<RavnSubnav />, { wrapper: wrapWithServices(services()) });
    expect(container.firstChild).toBeNull();
  });

  it('renders null for /ravn/sessions because the page owns the split rail', () => {
    mockPathname.mockReturnValue('/ravn/sessions');
    const { container } = render(<RavnSubnav />, { wrapper: wrapWithServices(services()) });
    expect(container.firstChild).toBeNull();
  });

  it('renders null for /ravn/budget', () => {
    mockPathname.mockReturnValue('/ravn/budget');
    const { container } = render(<RavnSubnav />, { wrapper: wrapWithServices(services()) });
    expect(container.firstChild).toBeNull();
  });
});
