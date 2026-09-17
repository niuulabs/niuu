import { useState } from 'react';
import { Dialog, DialogContent } from '@niuulabs/ui';
import { AdvancedLaunchWizard } from './AdvancedLaunchWizard';
import { QuickLaunch } from './QuickLaunch';
import type { ForgeStandardId } from './quickLaunchModel';
import type { LaunchWizardProps } from './LaunchWizardSteps';
export * from './launchWizardModel';
export * from './LaunchWizardSteps';

export function LaunchWizard(props: LaunchWizardProps & { initialStandard?: ForgeStandardId }) {
  const [advanced, setAdvanced] = useState(false);
  if (!props.open) return null;
  const close = (open: boolean) => {
    if (!open) setAdvanced(false);
    props.onOpenChange(open);
  };
  if (advanced || props.initialLaunchSpecRef)
    return <AdvancedLaunchWizard {...props} onOpenChange={close} />;
  return (
    <Dialog open={props.open} onOpenChange={close}>
      <DialogContent
        title="Quick launch"
        description="Choose a Forge and a workspace, then start your session."
        className="vol-quick__modal"
      >
        <QuickLaunch
          initialStandard={props.initialStandard}
          onAdvanced={() => setAdvanced(true)}
          onCreated={() => close(false)}
        />
      </DialogContent>
    </Dialog>
  );
}
