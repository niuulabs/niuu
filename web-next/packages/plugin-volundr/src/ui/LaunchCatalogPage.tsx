import { useState } from 'react';
import { AdvancedLaunchCatalogPage } from './AdvancedLaunchCatalogPage';
import { AdvancedLaunchWizard } from './AdvancedLaunchWizard';
import { QuickLaunch } from './QuickLaunch';
import './QuickLaunch.css';

export function LaunchCatalogPage() {
  const [custom, setCustom] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  if (custom)
    return (
      <>
        <div className="vol-quick niuu:p-4">
          <button className="vol-quick__button" onClick={() => setCustom(false)}>
            Back to standards
          </button>
        </div>
        <AdvancedLaunchCatalogPage />
      </>
    );
  return (
    <div className="vol-quick vol-quick-page" data-testid="launch-catalog-page">
      <header>
        <h1>Launch catalogue</h1>
        <p>Two standards. Your Forge, your workspace. Ready in a few clicks.</p>
      </header>
      <QuickLaunch onAdvanced={() => setAdvanced(true)} />
      <div className="vol-quick__section">
        <h2>Custom runtimes</h2>
        <p>
          Saved templates and the full runtime editor, including resources, extensions and rules.
        </p>
        <div>
          <button className="vol-quick__button" onClick={() => setCustom(true)}>
            Manage custom catalogue
          </button>
        </div>
      </div>
      {advanced && <AdvancedLaunchWizard open={advanced} onOpenChange={setAdvanced} />}
    </div>
  );
}
