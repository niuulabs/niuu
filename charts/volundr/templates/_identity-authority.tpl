{{- define "volundr.identityAuthorityKwargs" -}}
authority_url: {{ required "identityAuthority.url must identify the canonical HTTPS identity service" .Values.identityAuthority.url | quote }}
timeout: {{ .Values.identityAuthority.timeout }}
{{- if .Values.identityAuthority.caFile }}
ca_file: {{ .Values.identityAuthority.caFile | quote }}
{{- end }}
{{- end -}}
