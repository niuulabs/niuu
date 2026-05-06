// Components
export { Modal } from './components/Modal';
export type { ModalProps } from './components/Modal';

export { ProgressRing } from './components/ProgressRing';
export type { ProgressRingProps } from './components/ProgressRing';

export { MetricCard } from './components/MetricCard';
export type { MetricCardProps } from './components/MetricCard';

export { FilterTabs } from './components/FilterTabs';
export type { FilterTabsProps } from './components/FilterTabs';

export { SearchInput } from './components/SearchInput';
export type { SearchInputProps } from './components/SearchInput';

export { ResourceBar } from './components/ResourceBar';
export type { ResourceBarProps } from './components/ResourceBar';

export { StatusBadge } from './components/StatusBadge';
export type { StatusBadgeProps } from './components/StatusBadge';

export { StatusDot } from './components/StatusDot';
export type { StatusDotProps } from './components/StatusDot';

export { CollapsibleSection } from './components/CollapsibleSection';
export type { CollapsibleSectionProps, AccentColor } from './components/CollapsibleSection';

export { LoadingIndicator } from './components/LoadingIndicator';
export type { LoadingIndicatorProps } from './components/LoadingIndicator';

export { Toggle } from './components/Toggle';
export type { ToggleProps } from './components/Toggle';

export { AppShell } from './components/AppShell';
export type { AppShellProps } from './components/AppShell';
export { Sidebar } from './components/AppShell';
export type { SidebarProps } from './components/AppShell';

// Utils
export { cn } from './utils/classnames';

// API Client
export { createApiClient, getAccessToken, setTokenProvider, ApiClientError } from './api/client';
export type { ApiClient, ApiError } from './api/client';

// Registry
export { registerModule, getModule, getAllModules } from './registry';
export { registerProductModule, getProductModules } from './registry';
export type { ModuleEntry, ProductModule } from './registry';
export { resolveIcon } from './registry';
