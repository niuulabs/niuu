import { Drawer, DrawerContent, HighlightedCode, MarkdownContent } from '@niuulabs/ui';
import { useValkyrieSkill } from '../application/useValkyrieSkills';
import { timeAgo } from './reviewFormat';

const SECTION_LABEL =
  'niuu:text-[11px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-muted';

const SKILL_VIEWER_WIDTH = 560;

/**
 * A learned-skill reference rendered as a clickable name. Used wherever a
 * judgment, decision, or learning entry mentions a skill by name.
 */
export function SkillNameButton({
  name,
  onOpen,
}: {
  name: string;
  onOpen: (name: string) => void;
}) {
  return (
    <button
      type="button"
      data-testid={`skill-link-${name}`}
      aria-label={`View learned skill ${name}`}
      onClick={() => onOpen(name)}
      className="niuu:font-mono niuu:text-xs niuu:text-brand niuu:underline niuu:decoration-dotted niuu:underline-offset-2 niuu:hover:decoration-solid"
    >
      {name}
    </button>
  );
}

/**
 * Right-hand drawer showing everything an operator needs to judge a learned
 * skill: description, adoption info, requirements, the skill markdown, and
 * the tool/test code the resident built.
 */
export function SkillViewer({
  environmentId,
  skillName,
  onClose,
}: {
  environmentId: string;
  skillName: string | null;
  onClose: () => void;
}) {
  const { data: skill, isLoading, error } = useValkyrieSkill(environmentId, skillName);

  return (
    <Drawer
      open={Boolean(skillName)}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DrawerContent title={skillName ?? 'Learned skill'} width={SKILL_VIEWER_WIDTH}>
        <div data-testid="skill-viewer" className="niuu:flex niuu:flex-col niuu:gap-4 niuu:text-sm">
          {isLoading ? (
            <p data-testid="skill-viewer-loading" className="niuu:text-text-muted">
              Loading skill…
            </p>
          ) : null}
          {error ? (
            <p
              role="alert"
              className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-critical-bo niuu:bg-critical-bg niuu:p-2 niuu:text-xs niuu:text-critical"
            >
              {error instanceof Error ? error.message : 'Unable to load this skill'}
            </p>
          ) : null}
          {!isLoading && !error && !skill ? (
            <p data-testid="skill-viewer-missing" className="niuu:text-text-muted">
              This skill is no longer installed on {environmentId}. It was referenced in a past
              judgment but has since been rolled back or retired, so its code is not available to
              view.
            </p>
          ) : null}
          {skill ? (
            <>
              <p className="niuu:leading-6 niuu:text-text-primary">{skill.description}</p>
              <p className="niuu:text-xs niuu:text-text-muted">
                Adopted {timeAgo(skill.adoptedAt)} ago by {skill.valkyrieId} · learning{' '}
                {skill.learningId}
              </p>
              {skill.requirements.length > 0 ? (
                <div>
                  <div className={SECTION_LABEL}>requirements</div>
                  <div className="niuu:mt-2 niuu:flex niuu:flex-wrap niuu:gap-2">
                    {skill.requirements.map((requirement) => (
                      <span
                        key={requirement}
                        className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:font-mono niuu:text-[11px] niuu:text-text-secondary"
                      >
                        {requirement}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}
              {skill.content ? (
                <div>
                  <div className={SECTION_LABEL}>skill</div>
                  <div
                    data-testid="skill-viewer-content"
                    className="niuu:mt-2 niuu:max-h-80 niuu:overflow-auto niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:px-4 niuu:py-3 niuu:text-text-primary"
                  >
                    <MarkdownContent content={skill.content} />
                  </div>
                </div>
              ) : null}
              {skill.toolCode ? (
                <div>
                  <div className={SECTION_LABEL}>tool code</div>
                  <HighlightedCode
                    className="niuu:mt-2"
                    code={skill.toolCode}
                    lang="python"
                    testId="skill-tool-code"
                  />
                </div>
              ) : null}
              {skill.testCode ? (
                <div>
                  <div className={SECTION_LABEL}>tests</div>
                  <HighlightedCode
                    className="niuu:mt-2"
                    code={skill.testCode}
                    lang="python"
                    testId="skill-test-code"
                  />
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      </DrawerContent>
    </Drawer>
  );
}
