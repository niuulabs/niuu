# Configure Your Project

Connect real repositories, credentials, providers, and runtime defaults.

Project configuration decides what Niuu can launch, what credentials are available, which models can be used, and how operators review the work.

## Configure in this order

1. Add or verify the repository target.
2. Configure model providers through Bifröst or service settings.
3. Add credentials and secrets through the configured credential store.
4. Define launch presets for common workspace shapes.
5. Add workflow templates when work should move through Ting.

## Keep local files out of screenshots and sessions

Do not mount broad home directories or secret-heavy repos into demo workspaces. Prefer scoped credentials, throwaway repos, and explicit launch presets.

## Related pages

- [Configuration reference](../reference/configuration.md)
- [Credentials and secrets](../reference/credentials-and-secrets.md)
- [Model routing](../concepts/model-routing.md)
