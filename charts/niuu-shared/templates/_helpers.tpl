{{/*
Expand the name of the chart.
*/}}
{{- define "niuu-shared.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "niuu-shared.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "niuu-shared.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "niuu-shared.labels" -}}
helm.sh/chart: {{ include "niuu-shared.chart" . }}
{{ include "niuu-shared.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: shared-services
app.kubernetes.io/part-of: niuu
{{- end }}

{{/*
Selector labels
*/}}
{{- define "niuu-shared.selectorLabels" -}}
app.kubernetes.io/name: {{ include "niuu-shared.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "niuu-shared.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "niuu-shared.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Return the proper image name (global overrides local)
*/}}
{{- define "niuu-shared.image" -}}
{{- $registryName := .Values.image.registry -}}
{{- $repositoryName := .Values.image.repository -}}
{{- $tag := .Values.image.tag | default .Chart.AppVersion -}}
{{- if and .Values.global .Values.global.image -}}
  {{- if .Values.global.image.registry -}}
    {{- $registryName = .Values.global.image.registry -}}
  {{- end -}}
  {{- if .Values.global.image.tag -}}
    {{- $tag = .Values.global.image.tag -}}
  {{- end -}}
{{- end -}}
{{- if $registryName }}
{{- printf "%s/%s:%s" $registryName $repositoryName $tag -}}
{{- else }}
{{- printf "%s:%s" $repositoryName $tag -}}
{{- end }}
{{- end }}

{{/*
Return image pull secrets (global, converts strings to objects)
*/}}
{{- define "niuu-shared.imagePullSecrets" -}}
{{- $secrets := list -}}
{{- if and .Values.global .Values.global.imagePullSecrets -}}
  {{- $secrets = .Values.global.imagePullSecrets -}}
{{- end -}}
{{- if $secrets -}}
imagePullSecrets:
  {{- range $secrets }}
  - name: {{ . }}
  {{- end }}
{{- end -}}
{{- end }}

{{/*
Return the database secret name.
*/}}
{{- define "niuu-shared.databaseSecretName" -}}
{{- if .Values.database.existingSecret }}
{{- .Values.database.existingSecret }}
{{- else }}
{{- printf "%s-niuu-shared-db" .Release.Name }}
{{- end }}
{{- end }}

{{/*
Return the database host.
*/}}
{{- define "niuu-shared.databaseHost" -}}
{{- if .Values.database.external.enabled }}
{{- .Values.database.external.host }}
{{- else }}
{{- printf "%s-postgresql" .Release.Name }}
{{- end }}
{{- end }}

{{/*
Return the database port.
*/}}
{{- define "niuu-shared.databasePort" -}}
{{- if .Values.database.external.enabled }}
{{- .Values.database.external.port | default 5432 }}
{{- else }}
{{- 5432 }}
{{- end }}
{{- end }}

{{/*
Annotations for checksum/config - forces restart on config changes
*/}}
{{- define "niuu-shared.checksumAnnotations" -}}
checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
{{- if .Values.migrations.enabled }}
checksum/migrations: {{ include (print $.Template.BasePath "/migrations-configmap.yaml") . | sha256sum }}
{{- end }}
{{- if .Values.envoy.enabled }}
checksum/envoy: {{ include (print $.Template.BasePath "/envoy-configmap.yaml") . | sha256sum }}
{{- end }}
{{- end }}
