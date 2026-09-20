# External packages and integration definitions

Niuu can extend its integration catalog from deployment-owned YAML or JSON
files. This keeps private, site-specific, or separately licensed adapters out
of the Niuu repository and published image.

Packages may instead contribute infrastructure components such as VM providers
without appearing in the user Connections catalog. These trusted packages use
a versioned manifest and remain inactive until their adapter class is selected
in the corresponding Niuu configuration.

Configure one or more absolute paths under `integrations.definition_files`:

```yaml
integrations:
  definition_files:
    - /opt/niuu-private/acme-tracker/integration.yaml
```

In single-host Docker mode, configure the host package instead. `niuu up`
renders the read-only mount, container definition paths, and Python import path:

```yaml
docker:
  external_integrations:
    - source_dir: ~/.niuu/private-integrations/acme-tracker
      definition_files:
        - integration.yaml
```

A compute-only package supplies a manifest instead of catalog definitions:

```yaml
docker:
  external_integrations:
    - source_dir: ~/git/acme/niuu-modules/machines
      manifest_file: niuu-module.yaml
```

Provider credentials can remain outside both the module and the generated
Compose bundle. Mount an operator-owned file at an explicit container path and
select a file-backed authentication adapter in `compute.auth`:

```yaml
docker:
  read_only_files:
    - source_file: /absolute/host/path/acme-api-key
      target_file: /run/secrets/niuu/acme-api-key
compute:
  auth:
    adapter: niuu.adapters.outbound.http_auth.FileBearerTokenAuthAdapter
    kwargs:
      token_file: /run/secrets/niuu/acme-api-key
```

Both paths must be absolute and each container target must be unique. Niuu
passes the source path directly to Docker as a read-only bind mount; it does
not open or copy the credential while rendering the bundle. Protect the host
file with mode `0600` and keep it outside version control.

For an OAuth2/OIDC service client, mount its client secret the same way and use
`niuu.adapters.outbound.http_auth.ClientCredentialsBearerTokenAuthAdapter` with
`token_url`, `client_id`, and `client_secret_file`. Obtain the confidential
client, permitted scopes/audience, and grant from the service owner; do not
reuse a public browser client as an unattended backend identity.

Version 1 manifests declare typed component classes. Niuu imports each class at
startup and verifies its core port without constructing it:

```yaml
schema_version: 1
id: acme-machines
requires:
  niuu_machine_provider: 1
  niuu_vm_runtime: 1
components:
  - kind: machine_provider
    name: acme
    adapter: acme_machines.provider.AcmeMachineProvider
  - kind: vm_runtime
    name: acme-ssh
    adapter: acme_machines.runtime.AcmeVmRuntime
integration_definition_files: []
```

Supported component kinds are `machine_provider` and `vm_runtime`. Unknown
versions or kinds, incompatible contract versions, duplicate package/component
names, import failures, abstract classes, and classes implementing the wrong
port fail startup. A manifest can optionally name catalog files relative to
itself with `integration_definition_files`; the legacy `definition_files`
configuration remains supported.

The standalone compute CLI accepts the same validation gate with a repeatable
`--module-manifest /path/to/niuu-module.yaml` option. Its package root must
already be on `PYTHONPATH`, just as it is for a mounted Volundr deployment.

The source directory is a Docker-host path. It does not have to be visible at
that same path inside the running control-plane container; the generated bind
mount provides the package at its managed `/opt/niuu-external-integrations/...`
destination.

## Manage packages in Settings

An administrator can also register packages under **Settings → Runtime →
External integrations**. UI-managed packages must live below the active
install's `private-integrations` data directory (normally
`~/.niuu/data/private-integrations`). That directory is already visible to the
control plane, so Niuu can validate the definition files and import every named
adapter before changing the stack.

The UI supports:

- validating a package without restarting;
- adding a validated package to the persisted stack overrides;
- listing the definitions found in each registered package; and
- unregistering a package and restarting the platform.

Unregistering does not delete the package directory. The package remains
machine-local and can be registered again. Use the YAML form above when an
advanced Docker deployment needs to mount a source directory outside the Niuu
data directory. Helm/Kubernetes installs remain deployment-managed through
values, volumes, and mounts rather than accepting node-local paths from the UI.

Each file can contain one definition, a list of definitions, or a
`definitions` list:

```yaml
slug: acme-tracker
name: Acme Tracker
integration_type: issue_tracker
adapter: acme_tracker.adapter.AcmeTrackerAdapter
auth_type: api_key
credential_schema:
  required: [access_token]
  properties:
    access_token:
      label: Access token
      type: password
config_schema:
  required: [projects]
  properties:
    projects:
      label: Allowed projects
      type: string[]
```

The file contains catalog metadata only. Mount the adapter package separately
and make it importable by every process that constructs the adapter. For a
Python adapter this commonly means mounting its package read-only and adding
the mount's parent to `PYTHONPATH`. In split-service deployments the
integrations service needs the definition and adapter for connection tests,
while Ting also needs the adapter package to browse and dispatch tracker work.

External Python modules are trusted control-plane code, not sandboxed plugins.
They run with the same process credentials and network access as Volundr. Only
mount packages controlled and reviewed by the deployment operator.

External definitions are appended in configured file order. A duplicate slug
fails startup so an external mount cannot silently replace a built-in
integration. An operator can deliberately enable replacement with
`integrations.allow_definition_overrides: true`.

For Helm deployments, set `integrations.definitionFiles` and/or
`integrations.moduleManifestFiles`, and provide the files and adapter code
through `extraVolumes` and `extraVolumeMounts`. Ensure the package root is on
the container's `PYTHONPATH`. Do not put
credentials in a definition file or image. Credential schemas are rendered in
the Integrations UI; the submitted secret is stored through Niuu's configured
credential store.
