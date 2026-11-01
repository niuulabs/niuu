# Use a repository and save a reusable launch

Complete the [empty-workspace quick start](first-local-stack.md) first. Then
change one thing: give the same working runtime a repository. Only save a
reusable configuration once that launch works.

## Use a local repository in mini mode

Use a repository directory on the machine running Niuu. A local mount is direct
access to that directory: edits appear there, rather than in a new clone.

To create a small practice repository without a hosted Git account:

```bash
NIUU_PRACTICE_DIR=$(mktemp -d)
git -C "$NIUU_PRACTICE_DIR" init
printf '# Niuu practice\n' > "$NIUU_PRACTICE_DIR/README.md"
git -C "$NIUU_PRACTICE_DIR" add README.md
git -C "$NIUU_PRACTICE_DIR" -c user.name='Niuu Quick Start' \
  -c user.email='quickstart@example.invalid' commit -m 'Add practice README'
printf 'Workspace path: %s\n' "$NIUU_PRACTICE_DIR"
```

The name and email above identify only this practice commit; they do not change
your global Git settings or authenticate with any service.

1. Open **Völundr → Forge → custom launch…**.
2. Under **Source**, choose **local mount**.
3. Paste the printed absolute **Workspace path** into **Path**. Do not paste the
   literal variable name. Give the session a distinct name, such as `niuu-practice`.
4. Under **Runtime**, use the same Claude Code, model, and Local Forge choices
   that worked in the quick start.
5. Set **Initial prompt** to `Append the line "Edited through Niuu." to README.md.
   Do not commit the change.`
6. Review **Confirm** and click **forge session**.

Inspect **Files** and **Diffs**, then independently inspect the working tree:

```bash
git -C "$NIUU_PRACTICE_DIR" diff -- README.md
```

Expect one added line, `Edited through Niuu.` Stop the session when finished.
If the launch reports that the path is outside an allowed mount prefix, use a
path permitted by your operator's local mount configuration. Do not assume a
local mount path exists inside a remote cluster or OpenShell sandbox.

## Configure a hosted Git provider

The repository picker reads `/api/v1/niuu/repos`. Niuu builds that catalog from
configured GitHub organizations or GitLab groups, plus eligible user integration
connections. It does not scan your local checkouts or automatically import the
repositories from your `gh` login.

For a first local example, use the public repositories in the `niuulabs` GitHub
organization. Stop the foreground platform with Ctrl+C, then run these commands
in the terminal where you will start it:

```bash
export GIT__GITHUB__INSTANCES='[{"name":"GitHub","base_url":"https://api.github.com","orgs":["niuulabs"]}]'
niuu platform up
```

This setting is read by the shared repository service and Forge. It uses the
service-level `GIT__` prefix, **not** `NIUU_GIT__`. An instance entry creates the
provider, and `orgs` tells it which organizations to list. Enabling GitHub without
any organizations can leave the picker empty.

From another terminal, inspect the catalog:

```bash
curl --fail --silent --show-error http://127.0.0.1:8080/api/v1/niuu/repos
```

Expect repositories grouped under `GitHub`, with clone URLs and default branches.
On shared deployments this response is also scoped to the caller's configured
integration access. If the catalog is empty, inspect platform logs for provider
errors before trying to launch.

### Authenticate discovery and private access

Public GitHub reads work without a token until the unauthenticated API limit is
reached. For authenticated reads or private repositories, give the provider a
real token with access to the selected organization and repositories. Read it
without placing its value in shell history, then start Niuu from the same terminal:

```bash
printf 'GitHub access token: '
read -rs GIT__GITHUB__TOKEN
printf '\n'
export GIT__GITHUB__TOKEN
niuu platform up
```

Stop any already-running foreground host before starting this one. Keep the
`GIT__GITHUB__INSTANCES` setting exported as well. The token is applied to provider
instances that do not specify their own token source. Merely logging in with
`gh auth login` does not export this variable to Niuu.

For your own organization, replace `niuulabs` in `orgs` with its actual slug.
GitLab uses `GIT__GITLAB__INSTANCES` and `GIT__GITLAB__TOKEN`; its `base_url` is the
GitLab server origin, and its `orgs` entries identify groups. For GitHub Enterprise,
use the server's API base URL rather than the ordinary browser repository URL.

### Keep the provider configuration between restarts

Merge this section into `~/.niuu/config.yaml`, keeping the host's existing settings:

```yaml
git:
  github:
    instances:
      - name: GitHub
        base_url: https://api.github.com
        orgs:
          - niuulabs
        token_env: GIT__GITHUB__TOKEN
```

Set the config path in the process environment so the host and both service
settings loaders see it from startup:

```bash
unset GIT__GITHUB__INSTANCES
NIUU_CONFIG="$HOME/.niuu/config.yaml" niuu platform up
```

Keep `GIT__GITHUB__TOKEN` exported when using authenticated access. Unsetting the
instance-list override lets the YAML list take effect. The `git` section is read
by the services; the CLI's host schema alone does not consume it. Setting
`NIUU_CONFIG` before startup aligns their configuration paths. Do not commit token values.

### Check the catalog before launching

| Result | Check |
| --- | --- |
| No provider or repository entries | Instance configuration and a nonempty `orgs` list |
| API rate-limit error in logs | Supply authenticated provider access and retry when allowed |
| Private repository missing | Token's repository access and any organization authorization requirements |
| Branch list missing | Repository visibility and branch API access |
| Catalog works but clone fails | Credential delivery and network access on the actual runtime target |

## Clone a repository from the catalog

1. Reopen **Völundr → Forge → custom launch…** after configuring the provider.
2. Under **Source**, choose **git** and select the repository. For the public
   example, select the Niuu repository from the `niuulabs` organization.
3. Select an existing **Branch**, using the default branch reported by the catalog
   unless you intend to work on another branch. The source requires a branch.
4. Choose the runtime and target that worked in the quick start. Ask the agent to
   read the README and summarize the project before asking it to change files.
5. Launch, inspect the session's repository files, and verify the agent response.

When the picker is empty, the wizard offers a clone-URL input. That is an input
alternative, not a substitute for provider setup: Forge normally validates a Git
source through a configured provider. A public URL can therefore still fail with
“no git provider supports this repository URL.” Configure the matching provider
first. For a local checkout that needs no hosted provider, use **local mount**.

Model login does not grant GitHub or GitLab access. Catalog access and runtime
clone access are distinct: private repositories also need credentials delivered
through the chosen target's integration/credential path. For a remote target,
verify cloning there rather than relying on the host's Git configuration.

## Save the working configuration

A **launch spec** stores reusable launch settings. It is optional; it does not
run an agent by itself. A **session** is an individual execution created from
those settings. Older pages and some APIs call reusable settings presets or
profiles; the current wizard uses **Load launch spec**.

1. Open another custom launch and enter the Source and Runtime settings you have
   just verified.
2. On **Runtime**, find the **Launch spec** section at the top.
3. In the field with placeholder **save as launch spec**, enter a name such as
   `practice-claude`, then click **save**.
4. Close and reopen the wizard. Select the saved entry under **Load launch spec**.
5. Recheck the source, branch/path, runtime, model, and access selections on
   **Confirm**. Launch another session and repeat the small file-change check.

Do not assume saving a spec captured credentials or made a host path portable.
Review the loaded values before using it on another Forge target.
