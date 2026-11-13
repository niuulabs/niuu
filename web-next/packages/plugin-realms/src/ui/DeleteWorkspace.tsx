import { useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { SectionCard } from '@niuulabs/plugin-volundr';
import { TEARDOWN_STEPS, useDeleteRealm } from '../application/useDeleteRealm';

const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-text-primary niuu:disabled:opacity-40';
const DANGER =
  'niuu:rounded-md niuu:border niuu:border-critical-bo niuu:bg-critical-bg niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-critical-fg niuu:disabled:opacity-40';

/** Delete the realm: the create recipe backwards, two clicks, fail loud on the step that broke. */
export function DeleteWorkspace({ slug, realmName }: { slug: string; realmName: string }) {
  const navigate = useNavigate();
  const teardown = useDeleteRealm(slug);
  const [armed, setArmed] = useState(false);
  return (
    <SectionCard title="Delete the workspace" description="Gone from the list, for good.">
      <div className="niuu:flex niuu:flex-col niuu:gap-2" data-testid="realm-teardown">
        <span className="niuu:text-xs niuu:text-text-muted">
          Deleting the workspace removes its trust and capabilities. The memory instance stays (the
          platform cannot remove one yet); its pages are still under Memory › Library.
        </span>
        <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
          {armed ? (
            <>
              <button
                type="button"
                className={DANGER}
                disabled={teardown.running}
                onClick={() =>
                  teardown
                    .run()
                    .then(() => navigate({ to: '/realms' }))
                    .catch(() => undefined)
                }
                data-testid="realm-delete-confirm"
              >
                Yes, delete {realmName}
              </button>
              <button type="button" className={BUTTON} onClick={() => setArmed(false)}>
                Keep it
              </button>
            </>
          ) : (
            <button
              type="button"
              className={DANGER}
              onClick={() => setArmed(true)}
              data-testid="realm-delete"
            >
              Delete workspace
            </button>
          )}
        </div>
        {teardown.error ? (
          <span className="niuu:text-xs niuu:text-critical-fg">
            Stopped at “{TEARDOWN_STEPS.find((step) => step.id === teardown.failedStep)?.label}”:{' '}
            {teardown.error}. Nothing after that step was touched.
          </span>
        ) : null}
      </div>
    </SectionCard>
  );
}
