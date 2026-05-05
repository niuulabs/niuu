import { OverviewView } from './OverviewView';
import { PagesView } from './PagesView';
import { SourcesView } from './SourcesView';
import './MimirPage.css';
import './mimir-views.css';

type Tab = 'overview' | 'pages' | 'sources';

export function MimirPage({ defaultTab = 'overview' }: { defaultTab?: Tab } = {}) {
  return (
    <div className="mm-page-shell">
      {/* Shell topbar shows the title; subnav shows Overview/Pages/Sources links.
          No in-content tab bar needed — matches web2 which uses subnav only. */}
      {defaultTab === 'overview' && <OverviewView />}
      {defaultTab === 'pages' && <PagesView />}
      {defaultTab === 'sources' && <SourcesView />}
    </div>
  );
}
