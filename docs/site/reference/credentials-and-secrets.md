# Authenticate an agent

A healthy Niuu server does not mean its agents can call a model. Authentication
must be available to the process actually running the agent.

## Local Claude Code: subscription login

This is the path used by the [quick start](../get-started/first-local-stack.md).
In a terminal under the OS account that starts Niuu:

```bash
claude auth login
claude -p 'Reply with exactly OK.' --model sonnet --max-turns 1
```

Finish the browser login and confirm that the second command returns `OK` with
a successful exit code. Then start Niuu and choose **Claude Code** in the launch
wizard. Leave **Credentials** and **Integrations** empty for this local exercise.

The local adapter launches Skuld under the same OS account. Its Claude transport
uses subscription authentication by default. It removes `ANTHROPIC_API_KEY` and
`ANTHROPIC_AUTH_TOKEN` from the Claude child environment in that mode, so merely
exporting an API key does not switch this runtime to API billing.

If you see `OAuth access token has expired`, stop the session, sign in again,
and repeat the request above before launching a new session. A positive
`claude auth status` result is not proof that the token can make a request.

## Local Claude Code: API-key billing

Use this instead of the subscription path only when you intend to bill an
Anthropic API account. In Bash or Zsh, read the key without putting it into shell
history:

```bash
printf 'Anthropic API key: '
read -rs ANTHROPIC_API_KEY
printf '\n'
export ANTHROPIC_API_KEY
```

For Bash, `read -rs ANTHROPIC_API_KEY` suppresses input echo. If your shell does
not support that form, use your normal secret-manager environment injection.
Do not put the key into a prompt, screenshot, or committed YAML.

Start the platform from that terminal. In the launch wizard choose **Claude
Code**, then **Advanced → show advanced → Environment variables → add env var** and add `SKULD__CLAUDE_AUTH` with value
`api_key`. This selects the API-key authentication path in the Claude child
process. The key itself is inherited from the platform process in local mode.

Verify a small request inside the session. Do not infer success from the host
CLI alone or from a running session status. This billing path is separate from
the subscription quick start and is not covered by the subscription live check.

## OpenShell, Kubernetes, and connected providers

A local `claude auth login` does not place credentials into remote sandboxes.
On a deployment with provider enrollment configured, use **Settings →
Integrations → Connections** and choose **Claude Code (subscription)** or
**OpenAI Codex (ChatGPT)**. Complete the provider's browser/device flow and
verify an actual session afterward.

These connection buttons require a configured login runner and credential
store. An unconfigured local deployment cannot complete that enrollment flow.
The deployment procedure is documented in the repository's
[provider-login operator guide](https://github.com/niuulabs/niuu/blob/main/docs/operator/provider-logins.md).

For shared deployments, the selected connection and the session must belong to
the same Niuu user. Enable the relevant connection or select it explicitly under
**Access** when launching. Verify the provider/model and its reply in the
resulting session; a completed device challenge alone does not prove secret
delivery, token refresh, or inference.

## Credential storage

Niuu supports configured credential stores such as OpenBao/Vault and Infisical.
The launch wizard's **Credentials** list refers to entries already present in
that deployment's store; it is not a place to paste a key. Configure and verify
the store before relying on it for shared or remote workloads.

Use [Security and permissions](../operations/security-and-permissions.md) for
deployment access controls, and [Configuration](configuration.md) for service
configuration. Subscription login, API keys, repository authentication, and
platform login are separate credentials; setting one does not configure the
others.
