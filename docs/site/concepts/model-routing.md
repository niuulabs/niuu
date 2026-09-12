# Models and routing

A runtime is the software executing an agent. A model is the inference service
it calls. Bifröst provides a shared model catalog and gateway for clients
configured to use it.

## Catalog, provider, and alias

A provider describes where inference is served and how to authenticate. A model
identifies an available inference target. An alias gives clients a stable name
for a configured model choice.

Seeing a model in the catalog does not prove the provider accepts requests.
Validate connectivity and authentication with a real request from the client
that will use it. The runtime must also support that model's API and behavior.

## What goes through the gateway

Only clients configured to call Bifröst send inference traffic through it.
Launching Claude Code with its ordinary subscription login does not automatically
route that subscription through Bifröst. A model selector in the UI is not proof
that the gateway handled a request.

When a client uses the gateway, inspect the requested model or alias, selected
provider, and usage record. A local model keeps inference local only when the
configured endpoint is local; other tools used by the agent may still call
external services.

See [inspect model routing](../get-started/model-routing-step.md) and
[authentication](../reference/credentials-and-secrets.md).
