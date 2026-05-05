/**
 * RavnPage — overview route component at /ravn.
 *
 * Tabs are rendered by the shell topbar via PluginDescriptor.tabs.
 * This component is the page for the "Overview" tab.
 */

import { OverviewPage } from './OverviewPage';

export function RavnPage() {
  return (
    <div data-testid="ravn-page">
      <OverviewPage />
    </div>
  );
}
