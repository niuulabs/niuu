import { createRoute } from '@tanstack/react-router';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { SetupPage } from './ui/SetupPage';
import { ReadyPage } from './ui/ReadyPage';

export const setupPlugin = definePlugin({
  id: 'setup',
  rune: 'ᚦ',
  title: 'Setup',
  subtitle: 'first-launch wizard',
  system: true,
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/setup',
      component: SetupPage,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/ready',
      component: ReadyPage,
    }),
  ],
});

export { SetupGate, isSetupPath, SETUP_PATH, READY_PATH } from './ui/SetupGate';
export { SETUP_SERVICE_KEY } from './ui/hooks';
export { buildSetupHttpAdapter } from './adapters/http';
export { createMockSetupService, MOCK_CATALOG, MOCK_SYSTEM } from './adapters/mock';
export type { ISetupService } from './ports';
export type {
  ApplyStatus,
  CatalogEntry,
  ConnectIntegrationInput,
  Enrollment,
  EnrollmentState,
  HostFacts,
  ModelOption,
  StackChanges,
  StackSettings,
  StackView,
  IntegrationConnection,
  IntegrationTestResult,
  SetupState,
  SystemCheck,
  SystemReport,
} from './domain/setup';
