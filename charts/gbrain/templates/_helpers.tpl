{{- define "gbrain.name" -}}
{{- printf "%s-gbrain" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- define "gbrain.image" -}}
{{- printf "%s:%s" (required "image.repository is required; build containers/gbrain/Dockerfile" .Values.image.repository) (.Values.image.tag | default .Chart.AppVersion) -}}
{{- end -}}
{{- define "gbrain.labels" -}}
app.kubernetes.io/name: gbrain
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: niuu
app.kubernetes.io/component: knowledge-service
{{- end -}}
{{- define "gbrain.adminSecret" -}}
{{- default (printf "%s-admin" (include "gbrain.name" .)) .Values.existingSecret -}}
{{- end -}}
{{- define "gbrain.databaseEnv" -}}
{{- if .Values.postgres.enabled }}
- name: GBRAIN_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "gbrain.name" . }}-db-app
      key: uri
{{- end }}
{{- end -}}

{{/* Discovery metadata is separate from immutable workload selectors. */}}
{{- define "gbrain.niuuLabels" -}}
niuu.world/cluster: {{ .Values.niuu.cluster | quote }}
niuu.world/namespace: {{ .Release.Namespace | quote }}
niuu.world/entity-id: {{ .Values.niuu.instanceId | default .Release.Name | quote }}
{{- end -}}
