import { expect, test, type Page, type Route } from '@playwright/test';

const settingsUrl = '/settings/runtime/external-integrations?config=/config.live.json';
const packagesPath = '/api/v1/niuu/setup/settings/external-integrations';

const modulePackage = {
  id: '0',
  ok: true,
  sourceDir: '/var/lib/niuu/private-integrations/acme-machines',
  definitionFiles: [],
  manifestFile: 'niuu-module.yaml',
  moduleId: 'acme-machines',
  definitions: [],
  components: [
    {
      kind: 'machine_provider',
      name: 'acme',
      adapter: 'acme_machines.provider.AcmeMachineProvider',
    },
  ],
  errors: [],
};

function runtimeSettingsSchema() {
  return {
    title: 'Runtime',
    scope: 'admin',
    sections: [
      {
        id: 'external-integrations',
        label: 'External integrations',
        fields: [],
        resources: [
          {
            id: 'external-integration-packages',
            type: 'external_integrations',
            label: 'Integration packages',
            description: 'Private packages remain on this machine.',
            listPath: packagesPath,
            createPath: packagesPath,
            deletePath: `${packagesPath}/{id}`,
            validatePath: `${packagesPath}/validate`,
          },
        ],
      },
    ],
  };
}

async function mockRuntimeSettings(page: Page, handlePackages: (route: Route) => Promise<void>) {
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/v1/niuu/setup') {
      await route.fulfill({ json: { enabled: false, completed: true, steps: [] } });
      return;
    }
    if (path === '/api/v1/niuu/setup/settings') {
      await route.fulfill({ json: runtimeSettingsSchema() });
      return;
    }
    if (path.startsWith(packagesPath)) {
      await handlePackages(route);
      return;
    }
    await route.continue();
  });
}

test.describe('external module packages', () => {
  test('installs a manifest-only machine provider with keyboard navigation', async ({ page }) => {
    let installed = false;
    const requests: Array<{ path: string; body: unknown }> = [];
    await mockRuntimeSettings(page, async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() === 'GET') {
        await route.fulfill({
          json: {
            managedRoot: '/var/lib/niuu/private-integrations',
            items: installed ? [modulePackage] : [],
          },
        });
        return;
      }

      requests.push({ path, body: request.postDataJSON() });
      if (path.endsWith('/validate')) {
        await route.fulfill({ json: modulePackage });
        return;
      }

      installed = true;
      await route.fulfill({ status: 201, json: { ...modulePackage, applyState: 'applying' } });
    });

    await page.goto(settingsUrl);
    const directory = page.getByLabel('Package directory');
    const definitions = page.getByLabel('Definition files');
    const manifest = page.getByLabel('Module manifest');
    const validate = page.getByRole('button', { name: 'Validate package' });
    const add = page.getByRole('button', { name: 'Add and restart platform' });

    await expect(directory).toHaveValue('/var/lib/niuu/private-integrations/');
    await directory.fill(modulePackage.sourceDir);
    await manifest.fill(modulePackage.manifestFile);
    await directory.focus();
    await page.keyboard.press('Tab');
    await expect(definitions).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(manifest).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(validate).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(add).toBeFocused();

    await validate.focus();
    await page.keyboard.press('Enter');

    await expect(page.getByText('Package is valid')).toBeVisible();
    await expect(page.getByText('acme (machine provider)')).toBeVisible();
    expect(requests[0]).toEqual({
      path: `${packagesPath}/validate`,
      body: {
        sourceDir: modulePackage.sourceDir,
        definitionFiles: [],
        manifestFile: modulePackage.manifestFile,
      },
    });

    await add.focus();
    await page.keyboard.press('Enter');
    await expect(
      page.getByText(
        'Stack update started. The platform may be unavailable briefly while it restarts.',
      ),
    ).toBeVisible();
    await expect(page.getByText('acme-machines')).toBeVisible();
    await expect(page.getByText(/machine provider: acme/)).toBeVisible();
    expect(requests[1]).toEqual({
      path: packagesPath,
      body: {
        sourceDir: modulePackage.sourceDir,
        definitionFiles: [],
        manifestFile: modulePackage.manifestFile,
      },
    });
  });

  test('shows package inventory loading', async ({ page }) => {
    let releaseInventory: () => void = () => {};
    const inventoryReady = new Promise<void>((resolve) => {
      releaseInventory = resolve;
    });
    await mockRuntimeSettings(page, async (route) => {
      await inventoryReady;
      await route.fulfill({
        json: { managedRoot: '/var/lib/niuu/private-integrations', items: [] },
      });
    });

    await page.goto(settingsUrl);
    await expect(page.getByText('Loading external packages…')).toBeVisible();
    releaseInventory();
    await expect(page.getByText('No external integration packages loaded.')).toBeVisible();
  });

  test('keeps package inventory failures actionable', async ({ page }) => {
    await mockRuntimeSettings(page, async (route) => {
      await route.fulfill({ status: 503, json: { detail: 'Module inventory is unavailable' } });
    });

    await page.goto(settingsUrl);
    await expect(page.getByText(/Could not load external packages/)).toContainText(
      'Module inventory is unavailable',
    );
  });
});
