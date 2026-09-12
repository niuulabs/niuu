# Accept a shared deployment

A deployment is ready when its behavior has been verified under the identities,
network paths, storage, and runtime profiles it will actually use. A green Helm
release is only the beginning of that check.

| Area | Acceptance evidence |
| --- | --- |
| Identity | Login works through the intended gateway; invalid identities are rejected |
| Authorization | Two users see only the sessions and credentials permitted by policy |
| Provider access | A real session receives a model response using its configured credential path |
| Filesystem | The runtime can access intended paths and cannot obtain unintended mounts |
| Persistence | Required data survives a restart; deletion removes only the intended resources |
| Database | Backup and restore have been exercised against the selected schema version |
| Network | Browser streams and service calls work through supported authenticated routes |
| Failure handling | Failed launch leaves no unexpected owned resources; operators can find the error |
| Observability | Session/run/case IDs lead to useful logs and configured traces |
| Upgrade | Rendered changes and migrations are reviewed; recovery steps are known |

Test each enabled runtime profile. A local-process success does not certify
OpenShell or Kubernetes; two profiles on the same cluster can expose different
controls and persistence contracts.

Keep the evidence with the deployment's version and configuration revision.
The [quick-start verification](quickstart-verification.md) describes the automated
baseline and its provider-authentication limits.
