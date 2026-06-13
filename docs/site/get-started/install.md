# Install

Prepare a local Niuu development environment.

The fastest path is to use the repository scripts. They install or verify the required local tools, build the web packages, start PostgreSQL, and run the platform host.

## Prerequisites

- macOS or Linux
- `curl`, `make`, `gcc`, and `pkg-config`
- Network access for first-time dependency installation
- A checked-out copy of the repository

The `start-dev` script installs `uv`, `pnpm`, and Node.js if they are not already available in the expected local paths.

## Install dependencies

```bash
uv sync --all-extras --dev
pnpm --dir web-next install
```

Most operators can skip the manual install and go straight to `./start-dev`; the script performs these checks for you.

## Next step

Run the [first local stack](first-local-stack.md).
