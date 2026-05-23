{{/*
Expand the name of the chart.
*/}}
{{- define "mimir.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "mimir.fullname" -}}
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
{{- define "mimir.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "mimir.labels" -}}
helm.sh/chart: {{ include "mimir.chart" . }}
{{ include "mimir.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: knowledge-service
app.kubernetes.io/part-of: niuu
{{- end }}

{{/*
Selector labels
*/}}
{{- define "mimir.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mimir.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "mimir.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "mimir.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Return the proper image name (global overrides local)
*/}}
{{- define "mimir.image" -}}
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
{{- define "mimir.imagePullSecrets" -}}
{{- $secrets := list -}}
{{- if and .Values.global .Values.global.imagePullSecrets -}}
  {{- $secrets = .Values.global.imagePullSecrets -}}
{{- end -}}
{{- if .Values.imagePullSecrets -}}
  {{- $secrets = .Values.imagePullSecrets -}}
{{- end -}}
{{- if $secrets -}}
imagePullSecrets:
  {{- range $secrets }}
  - name: {{ . }}
  {{- end }}
{{- end -}}
{{- end }}

{{/*
Claim name for the wiki data PVC
*/}}
{{- define "mimir.pvcName" -}}
{{- if .Values.persistence.existingClaim -}}
{{ .Values.persistence.existingClaim }}
{{- else -}}
{{ include "mimir.fullname" . }}-data
{{- end -}}
{{- end }}

{{/*
Annotations for checksum/config - forces restart on config changes
*/}}
{{- define "mimir.checksumAnnotations" -}}
checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
{{- if .Values.envoy.enabled }}
checksum/envoy: {{ include (print $.Template.BasePath "/envoy-configmap.yaml") . | sha256sum }}
{{- end }}
{{- end }}
