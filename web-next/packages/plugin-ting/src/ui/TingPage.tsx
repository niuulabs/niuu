/**
 * TingPage — entry point for the /ting route.
 *
 * Delegates to DashboardPage; kept as a thin re-export so the route
 * descriptor in index.ts does not need to change.
 */
export { DashboardPage as TingPage } from './DashboardPage';
