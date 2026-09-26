{{- define "ting.identityAuthorityKwargs" -}}
authority_url: {{ required "identityAuthority.url must identify the canonical HTTPS identity service" .Values.identityAuthority.url | quote }}
timeout: {{ .Values.identityAuthority.timeout }}
{{- if .Values.identityAuthority.caFile }}
ca_file: {{ .Values.identityAuthority.caFile | quote }}
{{- end }}
{{- end -}}

{{/*
Envoy ext_authz deadline. When the central identity authority is enabled, the
authorization sidecar calls it before deciding, so the deadline covers that
call (identityAuthority.timeout, seconds) plus the decision budget
(envoy.authorization.timeout). Otherwise Envoy cancels the check first and
answers 503 while the identity lookup is still in flight.
*/}}
{{- define "ting.extAuthzTimeout" -}}
{{- $budget := toString .Values.envoy.authorization.timeout -}}
{{- if not (regexMatch "^[0-9]+(\\.[0-9]+)?(ms|s)$" $budget) -}}
{{- fail (printf "envoy.authorization.timeout must be a duration in s or ms (e.g. 1s, 500ms), got %q" $budget) -}}
{{- end -}}
{{- $seconds := 0.0 -}}
{{- if hasSuffix "ms" $budget -}}
{{- $seconds = divf (trimSuffix "ms" $budget | float64) 1000 -}}
{{- else -}}
{{- $seconds = trimSuffix "s" $budget | float64 -}}
{{- end -}}
{{- if .Values.identityAuthority.enabled -}}
{{- $seconds = addf $seconds .Values.identityAuthority.timeout -}}
{{- end -}}
{{- printf "%gs" $seconds -}}
{{- end -}}
