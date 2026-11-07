{{- /*
Envoy jwt_authn providers. `params` adds the query parameters browser WebSockets carry
their token in. The live-preview route uses the workload provider without them: there
the query string belongs to the app (Vite's hot-reload socket sends its own `?token=`),
and Envoy would reject that as a malformed JWT. Forge's router presents its credential
in Authorization.
*/ -}}
{{- define "skuld.envoy.keycloakJwtProvider" -}}
{{- $jwt := .root.Values.envoy.jwt -}}
{{- $headerNames := .root.Values.envoy.headerNames -}}
issuer: {{ $jwt.issuer | quote }}
{{- if $jwt.audiences }}
audiences:
  {{- range $jwt.audiences }}
  - {{ . | quote }}
  {{- end }}
{{- end }}
from_headers:
  - name: Authorization
    value_prefix: "Bearer "
{{- if .params }}
from_params:
  - token
  - access_token
{{- end }}
remote_jwks:
  http_uri:
    uri: {{ $jwt.jwksUri | quote }}
    cluster: keycloak
    timeout: {{ $jwt.jwksTimeout }}
  cache_duration:
    seconds: {{ $jwt.jwksCacheDurationSeconds }}
forward: true
payload_in_metadata: jwt_payload
claim_to_headers:
  - header_name: {{ $headerNames.userId }}
    claim_name: sub
  - header_name: {{ $headerNames.email }}
    claim_name: email
  - header_name: {{ $headerNames.tenant }}
    claim_name: {{ $jwt.tenantClaim }}
  - header_name: {{ $headerNames.roles }}
    claim_name: {{ $jwt.rolesClaim }}
  {{- range $jwt.extraClaimHeaders }}
  - header_name: {{ .headerName | quote }}
    claim_name: {{ .claimName | quote }}
  {{- end }}
{{- end }}

{{- define "skuld.envoy.workloadJwtProvider" -}}
{{- $workload := .root.Values.envoy.jwt.workload -}}
{{- $headerNames := .root.Values.envoy.headerNames -}}
issuer: {{ $workload.issuer | quote }}
{{- if $workload.audiences }}
audiences:
  {{- range $workload.audiences }}
  - {{ . | quote }}
  {{- end }}
{{- end }}
from_headers:
  - name: Authorization
    value_prefix: "Bearer "
{{- if .params }}
from_params:
  - token
{{- end }}
remote_jwks:
  http_uri:
    uri: {{ $workload.jwksUri | quote }}
    cluster: workload_jwks
    timeout: {{ $workload.jwksTimeout | default "5s" }}
  cache_duration:
    seconds: {{ $workload.jwksCacheDurationSeconds | default 300 }}
forward: true
payload_in_metadata: jwt_payload
claim_to_headers:
  - header_name: {{ $headerNames.userId }}
    claim_name: sub
  - header_name: {{ $headerNames.email }}
    claim_name: email
  - header_name: {{ $headerNames.tenant }}
    claim_name: {{ $workload.tenantClaim | default "tenant_id" }}
  - header_name: {{ $headerNames.roles }}
    claim_name: {{ $workload.rolesClaim | default "resource_access.volundr-api.roles" }}
  {{- range $workload.extraClaimHeaders }}
  - header_name: {{ .headerName | quote }}
    claim_name: {{ .claimName | quote }}
  {{- end }}
{{- end }}
