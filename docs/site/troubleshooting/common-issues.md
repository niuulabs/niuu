# Common Issues

Fix common local setup and operator issues.

## The local stack does not start

Check the log path printed by `./start-dev`, then verify required tools are available:

```bash
curl --version
make --version
gcc --version
pkg-config --version
```

## The UI URL does not open

Use the exact host printed by `./start-dev`. The script may bind to a LAN address instead of `127.0.0.1`.

## A direct plugin URL returns JSON

Some service names also exist as API mounts. Open the main UI URL first, then navigate through the left rail if a direct plugin URL collides with a backend route.

## A session can access more than expected

Stop the session and review the launch preset, mounted paths, credentials, and provider configuration before trying again.
