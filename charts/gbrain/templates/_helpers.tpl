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
