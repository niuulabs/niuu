# FAQ

Frequently asked questions about Niuu.

## Is Niuu self-hosted?

Yes. You can run it locally or deploy it into your own infrastructure.

## Does Niuu require cloud models?

No. Niuu can expose local models when configured. Cloud providers are optional and depend on your model routing setup.

## Is the local stack production-ready?

No. The local stack is for development and demos. Use Kubernetes deployment guidance and production hardening for shared use.

## Why does the UI show multiple service names?

Niuu is composed of services. The UI keeps those service boundaries visible so operators can understand what owns each part of the workflow.

## Where did the old docs go?

Older Volundr-first docs remain in `docs/archive/site-legacy` as rewrite source material. The published docs now focus on Niuu as the platform.
