# Connect Codex and Claude Code

In **Settings → Integrations → Connections**, choose **OpenAI Codex (ChatGPT)**
or **Claude Code (subscription)** and connect. Codex shows a device code to enter
on OpenAI's page. Claude opens its authorization page; paste the returned
authorization code into Niuu. Neither flow asks you to handle an API key or a
credential file.

Credentials are stored under the signed-in Niuu user's identity in the configured
credential store. Enabled connections are included automatically when launching
a session without an explicit integration selection. Codex's existing credential
broker refreshes its login and supplies access tokens to Skuld. Claude's token
is delivered through the existing secret-injection manifest as
`CLAUDE_CODE_OAUTH_TOKEN`; it is intended for Claude Code/Agent SDK sessions.
Use Reconnect if Claude requires authorization again. These tokens do not
support Claude Remote Control or claude.ai connectors. The separate Anthropic API connection remains available.

## Deployment

Configure the adapter in the service that actually serves the integrations API
(normally `niuu-shared`):

- `credentialEnrollmentRunner.adapter`:
  `volundr.adapters.outbound.k8s_login_runner.KubernetesLoginRunner`
- `credentialEnrollmentRunner.kwargs.image`: a pinned Skuld image containing
  `/opt/venv/bin/python`, `/usr/local/bin/codex`, and `/usr/local/bin/claude`.
- `credentialEnrollmentRunner.kwargs.namespace`: a dedicated namespace, such as
  `niuu-logins`. Do not use the control-plane namespace.

The shared and standalone Volundr charts create the namespace and narrowly
scoped Job/pod-exec permissions when that adapter is selected. Configure only
the integrations-serving release to own a particular login namespace. Keep the
shared credential store and Volundr broker pointed at the same OpenBao mount.
When Connections is served by `niuu-shared`, set Volundr's
`integrations.databaseName` to the shared database name (for example,
`niuu_shared`). This reuses Volundr's configured PostgreSQL server and credentials
so session creation reads the same user-owned connections as the Connections UI.
An empty value retains the standalone service database.
Deploy the updated API and web images and chart before enabling the adapter in
GitOps. The default adapter remains explicitly disabled for environments that
have not configured a login runner.

Each login runs the official CLI in a short-lived Kubernetes Job. Its only
writable volume is memory-backed `/tmp`; it has no persistent disk, repository,
existing credentials, service-account token, or OpenShell dependency. The API
supplies the worker code from its installed package, so its implementation does
not depend on the version of the Python package inside the CLI image.

The enrollment UUID identifies the Job across API replicas and restarts. Start
returns immediately; status polling discovers the provider challenge. Provider
output never goes to pod logs. The API reads completed credentials over pod exec,
persists them to the credential store, and deletes the Job. Browser authorization
codes travel through exec stdin, not Kubernetes command/audit arguments.
Kubernetes enforces the enrollment deadline and garbage-collects the Job even
when the API or initiating browser is gone.

## Verification and failures

Verify both signing in and starting a real session for each provider after
deployment; obtaining a login challenge alone does not verify credential
capture, OpenBao delivery, or inference. Device login may need enabling in the
user's ChatGPT security/workspace settings.

Worker startup failures appear as enrollment failures. Inspect Job/pod status
and events in the login namespace for image-pull, scheduling, or permission
errors. Do not log or export the worker's credential files. A cancelled, expired,
or failed attempt can be restarted with Reconnect. An API rollout does not
restart the provider login or change its code.

Provider references:

- [Codex app-server authentication](https://learn.chatgpt.com/docs/app-server#auth-endpoints)
- [Claude Code setup-token](https://code.claude.com/docs/en/authentication#generate-a-long-lived-token)
