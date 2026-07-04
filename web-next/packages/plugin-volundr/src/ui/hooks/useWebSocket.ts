/**
 * `useWebSocket` now lives in `@niuulabs/ui` (promoted once a second plugin
 * needed the Skuld chat machinery). This module re-exports it so existing
 * plugin-volundr import paths keep working unchanged.
 */
export { useWebSocket } from '@niuulabs/ui';
