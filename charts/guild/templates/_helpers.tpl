{{/*
Expand the name of the chart.
*/}}
{{- define "guild.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "guild.fullname" -}}
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
{{- define "guild.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Niuu deployment discovery labels.
*/}}
{{- define "guild.niuuLabels" -}}
{{- $cluster := "unknown" -}}
{{- if and .Values.global .Values.global.niuu .Values.global.niuu.cluster -}}
{{- $cluster = .Values.global.niuu.cluster -}}
{{- else if and .Values.niuu .Values.niuu.cluster -}}
{{- $cluster = .Values.niuu.cluster -}}
{{- end -}}
niuu.world/cluster: {{ $cluster | quote }}
niuu.world/namespace: {{ .Release.Namespace | quote }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "guild.labels" -}}
helm.sh/chart: {{ include "guild.chart" . }}
{{ include "guild.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{ include "guild.niuuLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: guild
app.kubernetes.io/part-of: niuu
{{- end }}

{{/*
Selector labels
*/}}
{{- define "guild.selectorLabels" -}}
app.kubernetes.io/name: {{ include "guild.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "guild.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "guild.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Return the proper image name (global overrides local)
*/}}
{{- define "guild.image" -}}
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
{{- define "guild.imagePullSecrets" -}}
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
{{- define "guild.databaseSecretName" -}}
{{- if .Values.database.existingSecret }}
{{- .Values.database.existingSecret }}
{{- else }}
{{- printf "%s-guild-db" .Release.Name }}
{{- end }}
{{- end }}

{{/*
Return the database host.
*/}}
{{- define "guild.databaseHost" -}}
{{- if .Values.database.external.enabled }}
{{- .Values.database.external.host }}
{{- else }}
{{- printf "%s-postgresql" .Release.Name }}
{{- end }}
{{- end }}

{{/*
Return the database port.
*/}}
{{- define "guild.databasePort" -}}
{{- if .Values.database.external.enabled }}
{{- .Values.database.external.port | default 5432 }}
{{- else }}
{{- 5432 }}
{{- end }}
{{- end }}

{{/*
Annotations for checksum/config - forces restart on config changes
*/}}
{{- define "guild.checksumAnnotations" -}}
checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
{{- if .Values.migrations.enabled }}
checksum/migrations: {{ include (print $.Template.BasePath "/migrations-configmap.yaml") . | sha256sum }}
{{- end }}
{{- if .Values.envoy.enabled }}
checksum/envoy: {{ include (print $.Template.BasePath "/envoy-configmap.yaml") . | sha256sum }}
{{- end }}
{{- end }}
