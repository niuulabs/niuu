"""Model gateway session environment contract.

Leaf module (no volundr imports) so both ``volundr.config`` and the session
contributors can share these names without a circular import.
"""

MODEL_GATEWAY_URL_ENV = "SKULD__MODEL_GATEWAY__URL"
MODEL_GATEWAY_TOKEN_ENV = "SKULD__MODEL_GATEWAY__TOKEN"

#: Sent as the model-gateway bearer when the host declared ``auth_mode: none``
#: (or ``envoy``, where the token is ignored in favour of Envoy/XFCC trust —
#: see ``bifrost.adapters.auth.mesh.MeshAuthAdapter``). This is not a
#: credential: it carries no authority and is only ever accepted because the
#: gateway in that mode trusts every caller. It is named and documented
#: instead of being an unlabelled magic string precisely so it is never
#: mistaken for one. It is never valid against a 'pat'-protected gateway, and
#: is never sent under ``auth_mode: oidc`` (see ``ModelGatewayContributor.contribute``) — oidc
#: hosts cannot run the bifrost plugin at all yet for exactly this reason
#: (no per-session credential exists to mint here; see
#: ``cli.config.CLISettings._OIDC_UNCOVERED_PLUGINS['bifrost']``). Minting and
#: threading a real per-session credential is tracked follow-up work.
OPEN_GATEWAY_TOKEN = "niuu-open-gateway"
