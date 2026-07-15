import type { ResearchCampaignPageController } from './useResearchCampaignPage';
import { ResearchCampaignDrawer } from './ResearchCampaignDrawer';
import { ResearchCampaignHeader } from './ResearchCampaignHeader';
import { ResearchCampaignOverview } from './ResearchCampaignOverview';
import { ResearchCampaignSections } from './ResearchCampaignSections';

export function ResearchCampaignView({
  controller,
}: {
  controller: ResearchCampaignPageController;
}) {
  return (
    <div className="ting-research-detail">
      <ResearchCampaignHeader controller={controller} />
      <div className="ting-research-detail__canvas">
        <div className="ting-research-detail__content">
          <ResearchCampaignOverview controller={controller} />
          <ResearchCampaignSections controller={controller} />
        </div>
      </div>
      <ResearchCampaignDrawer controller={controller} />
    </div>
  );
}
