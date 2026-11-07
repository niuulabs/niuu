import { Boxes, FileText, GitPullRequest, NotebookText, TestTubes } from 'lucide-react';

/**
 * The picture for a realm template: what the workspace produces, not the word for it.
 * Product workspaces turn tickets into pull requests, QA workspaces run tests and
 * reproductions, docs workspaces write pages, a blank workspace has only its charter.
 */
export function TemplateIcon({
  templateId,
  size = 14,
}: {
  templateId: string | null | undefined;
  size?: number;
}) {
  switch (templateId) {
    case 'product-resident':
      return <GitPullRequest size={size} aria-hidden="true" />;
    case 'qa-resident':
      return <TestTubes size={size} aria-hidden="true" />;
    case 'docs-resident':
      return <FileText size={size} aria-hidden="true" />;
    case 'blank-resident':
      return <NotebookText size={size} aria-hidden="true" />;
    default:
      return <Boxes size={size} aria-hidden="true" />;
  }
}

export function templateLabel(templateId: string | null | undefined): string {
  switch (templateId) {
    case 'product-resident':
      return 'Product workspace';
    case 'qa-resident':
      return 'QA workspace';
    case 'docs-resident':
      return 'Docs workspace';
    default:
      return 'Workspace';
  }
}
