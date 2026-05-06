import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { FeatureManagementSection } from './FeatureManagementSection';

const mockGetFeatureModules = vi.fn();
const mockToggleFeature = vi.fn();

vi.mock('@/modules/shared/adapters/feature-catalog.adapter', () => ({
  featureCatalogService: {
    getFeatureModules: (...args: unknown[]) => mockGetFeatureModules(...args),
    toggleFeature: (...args: unknown[]) => mockToggleFeature(...args),
  },
}));

vi.mock('@/modules/icons', () => ({
  resolveIcon: () => {
    const FakeIcon = ({ className }: { className?: string }) => (
      <span data-testid="icon" className={className} />
    );
    return FakeIcon;
  },
}));

const mockFeatures = [
  {
    key: 'users',
    label: 'Users',
    icon: 'Users',
    scope: 'admin',
    enabled: true,
    defaultEnabled: true,
    adminOnly: true,
    order: 1,
  },
  {
    key: 'tokens',
    label: 'Access Tokens',
    icon: 'ShieldCheck',
    scope: 'user',
    enabled: false,
    defaultEnabled: true,
    adminOnly: false,
    order: 2,
  },
];

describe('FeatureManagementSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetFeatureModules.mockResolvedValue(mockFeatures);
  });

  it('renders the section heading', async () => {
    render(<FeatureManagementSection />);
    expect(screen.getByText('Feature Modules')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText('Users')).toBeInTheDocument();
    });
  });

  it('shows loading state initially', () => {
    mockGetFeatureModules.mockReturnValue(new Promise(() => {}));
    render(<FeatureManagementSection />);
    expect(screen.getByText('Loading features...')).toBeInTheDocument();
  });

  it('renders feature cards after loading', async () => {
    render(<FeatureManagementSection />);
    await waitFor(() => {
      expect(screen.getByText('Users')).toBeInTheDocument();
      expect(screen.getByText('Access Tokens')).toBeInTheDocument();
    });
  });

  it('shows admin-only badge for admin features', async () => {
    render(<FeatureManagementSection />);
    await waitFor(() => {
      expect(screen.getByText('admin-only')).toBeInTheDocument();
    });
  });

  it('renders scope tabs', async () => {
    render(<FeatureManagementSection />);
    expect(screen.getByText('All')).toBeInTheDocument();
    expect(screen.getByText('Admin')).toBeInTheDocument();
    expect(screen.getByText('User')).toBeInTheDocument();
  });

  it('filters by admin scope when Admin tab clicked', async () => {
    render(<FeatureManagementSection />);
    await waitFor(() => {
      expect(screen.getByText('Users')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Admin'));

    expect(screen.getByText('Users')).toBeInTheDocument();
    expect(screen.queryByText('Access Tokens')).not.toBeInTheDocument();
  });

  it('filters by user scope when User tab clicked', async () => {
    render(<FeatureManagementSection />);
    await waitFor(() => {
      expect(screen.getByText('Access Tokens')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('User'));

    expect(screen.queryByText('Users')).not.toBeInTheDocument();
    expect(screen.getByText('Access Tokens')).toBeInTheDocument();
  });

  it('shows empty state when no features match scope', async () => {
    mockGetFeatureModules.mockResolvedValue([{ ...mockFeatures[0], scope: 'admin' }]);
    render(<FeatureManagementSection />);
    await waitFor(() => {
      expect(screen.getByText('Users')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('User'));
    expect(screen.getByText('No features found.')).toBeInTheDocument();
  });

  it('calls toggleFeature when toggle button clicked', async () => {
    mockToggleFeature.mockResolvedValue({
      ...mockFeatures[0],
      enabled: false,
    });

    render(<FeatureManagementSection />);
    await waitFor(() => {
      expect(screen.getByText('Users')).toBeInTheDocument();
    });

    const toggleBtn = screen.getByLabelText('Toggle Users');
    fireEvent.click(toggleBtn);

    await waitFor(() => {
      expect(mockToggleFeature).toHaveBeenCalledWith('users', false);
    });
  });

  it('renders toggle buttons for each feature', async () => {
    render(<FeatureManagementSection />);
    await waitFor(() => {
      expect(screen.getByLabelText('Toggle Users')).toBeInTheDocument();
      expect(screen.getByLabelText('Toggle Access Tokens')).toBeInTheDocument();
    });
  });
});
