/**
 * WikilinkPill — renders a [[slug]] wikilink as an interactive chip.
 *
 * - Resolved links are clickable and navigate to the target page.
 * - Broken links (L05) are rendered with a distinct error style and a
 *   tooltip explaining the issue.
 */

interface WikilinkPillProps {
  slug: string;
  broken?: boolean;
  /** Called when the user clicks a resolved link. */
  onNavigate?: (path: string) => void;
}

export function WikilinkPill({ slug, broken = false, onNavigate }: WikilinkPillProps) {
  if (broken) {
    return (
      <span
        className="mm-wikilink mm-wikilink--broken"
        title={`Broken wikilink: [[${slug}]] — page does not exist (L05)`}
        aria-label={`broken link: ${slug}`}
      >
        [[{slug}]]
      </span>
    );
  }

  const handleClick = () => {
    onNavigate?.(slug);
  };

  return (
    <button
      type="button"
      className="mm-wikilink mm-wikilink--resolved"
      onClick={handleClick}
      aria-label={`navigate to ${slug}`}
    >
      [[{slug}]]
    </button>
  );
}
