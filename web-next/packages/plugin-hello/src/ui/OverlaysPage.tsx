import {
  Dialog,
  DialogContent,
  DialogTrigger,
  DialogClose,
  Drawer,
  DrawerContent,
  DrawerTrigger,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Tooltip,
  TooltipProvider,
} from '@niuulabs/ui';

export function OverlaysPage() {
  return (
    <TooltipProvider>
      <div className="niuu-p-6 niuu-max-w-[720px] niuu-flex niuu-flex-col niuu-gap-8">
        <h2 className="niuu-m-0">Overlay primitives</h2>

        <section>
          <h3 className="niuu-mt-0 niuu-mb-3">Dialog</h3>
          <Dialog>
            <DialogTrigger asChild>
              <button data-testid="dialog-trigger">Open dialog</button>
            </DialogTrigger>
            <DialogContent title="Confirm action" description="This action cannot be undone.">
              <p data-testid="dialog-body" className="niuu-m-0 niuu-text-text-secondary">
                Dialog body content.
              </p>
              <div className="niuu-flex niuu-gap-3 niuu-mt-4 niuu-justify-end">
                <DialogClose asChild>
                  <button data-testid="dialog-cancel">Cancel</button>
                </DialogClose>
              </div>
            </DialogContent>
          </Dialog>
        </section>

        <section>
          <h3 className="niuu-mt-0 niuu-mb-3">Drawer</h3>
          <Drawer>
            <DrawerTrigger asChild>
              <button data-testid="drawer-trigger">Open drawer</button>
            </DrawerTrigger>
            <DrawerContent title="Side panel">
              <p data-testid="drawer-body" className="niuu-m-0 niuu-text-text-secondary">
                Drawer body content.
              </p>
            </DrawerContent>
          </Drawer>
        </section>

        <section>
          <h3 className="niuu-mt-0 niuu-mb-3">Popover</h3>
          <Popover>
            <PopoverTrigger asChild>
              <button data-testid="popover-trigger">Open popover</button>
            </PopoverTrigger>
            <PopoverContent>
              <p data-testid="popover-body" className="niuu-m-0">
                Popover body content.
              </p>
            </PopoverContent>
          </Popover>
        </section>

        <section>
          <h3 className="niuu-mt-0 niuu-mb-3">Tooltip</h3>
          <Tooltip content="Tooltip content" delayMs={0}>
            <button data-testid="tooltip-trigger">Hover for tooltip</button>
          </Tooltip>
        </section>
      </div>
    </TooltipProvider>
  );
}
