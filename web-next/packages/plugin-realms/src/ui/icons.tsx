import { Boxes, FileText, GitPullRequest, NotebookText, TestTubes } from 'lucide-react';

/**
 * The picture for a realm template: what the resident produces, not the word for it.
 * Product residents turn tickets into pull requests, QA residents run tests and
 * reproductions, docs residents write pages, a blank resident has only its charter.
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
      return 'Product resident';
    case 'qa-resident':
      return 'QA resident';
    case 'docs-resident':
      return 'Docs resident';
    case 'blank-resident':
      return 'Resident';
    default:
      return 'Resident';
  }
}

/** Relative time in plain words for card footers. */
export function agoLabel(iso: string | undefined, now = Date.now()): string | null {
  if (!iso) return null;
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return null;
  const minutes = Math.max(0, Math.round((now - then) / 60_000));
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}
