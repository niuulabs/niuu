import './ResearchCampaignPage.css';
import { ResearchCampaignView } from './ResearchCampaignView';
import { useResearchCampaignPage } from './useResearchCampaignPage';

export * from './researchCampaignModel';
export * from './ResearchCampaignPresentation';

export function ResearchCampaignPage() {
  const controller = useResearchCampaignPage();
  const { campaign, isLoading, isError, error } = controller;

  if (isLoading) {
    return <div className="ting-research-detail__loading">Loading campaign…</div>;
  }

  if (isError || !campaign) {
    return (
      <div className="ting-research-detail__error">
        {error instanceof Error ? error.message : 'Campaign not found.'}
      </div>
    );
  }

  return <ResearchCampaignView controller={{ ...controller, campaign }} />;
}
