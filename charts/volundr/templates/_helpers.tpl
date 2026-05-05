{{/*
Expand the name of the chart.
*/}}
{{- define "volundr.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "volundr.fullname" -}}
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
{{- define "volundr.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "volundr.labels" -}}
helm.sh/chart: {{ include "volundr.chart" . }}
{{ include "volundr.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: api
app.kubernetes.io/part-of: volundr
{{- end }}

{{/*
Selector labels
*/}}
{{- define "volundr.selectorLabels" -}}
app.kubernetes.io/name: {{ include "volundr.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Sessions PVC name
*/}}
{{- define "volundr.sessionsPvcName" -}}
{{- printf "%s-sessions" (include "volundr.fullname" .) }}
{{- end }}

{{/*
Home PVC name
*/}}
{{- define "volundr.homePvcName" -}}
{{- printf "%s-home" (include "volundr.fullname" .) }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "volundr.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "volundr.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Return the proper image name (global overrides local)
*/}}
{{- define "volundr.image" -}}
{{- $registryName := .Values.image.registry -}}
{{- $repositoryName := .Values.image.repository -}}
{{- $tag := .Values.image.tag | default .Chart.AppVersion -}}
{{- if and .Values.global .Values.global.image -}}
  {{- if .Values.global.image.registry -}}
    {{- $registryName = .Values.global.image.registry -}}
  {{- end -}}
  {{- if .Values.global.image.repository -}}
    {{- $repositoryName = .Values.global.image.repository -}}
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
{{- define "volundr.imagePullSecrets" -}}
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
Return the database secret name
*/}}
{{- define "volundr.databaseSecretName" -}}
{{- if .Values.database.existingSecret }}
{{- .Values.database.existingSecret }}
{{- else }}
{{- printf "%s-db" (include "volundr.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Return the database host
*/}}
{{- define "volundr.databaseHost" -}}
{{- if .Values.database.external.enabled }}
{{- .Values.database.external.host }}
{{- else }}
{{- printf "%s-postgresql" .Release.Name }}
{{- end }}
{{- end }}

{{/*
Return the database port
*/}}
{{- define "volundr.databasePort" -}}
{{- if .Values.database.external.enabled }}
{{- .Values.database.external.port | default 5432 }}
{{- else }}
{{- 5432 }}
{{- end }}
{{- end }}

{{/*
Annotations for checksum/config - forces restart on config changes
*/}}
{{- define "volundr.checksumAnnotations" -}}
checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
{{- if .Values.envoy.enabled }}
checksum/envoy: {{ include (print $.Template.BasePath "/envoy-configmap.yaml") . | sha256sum }}
{{- end }}
{{- end }}

{{/*
Web component fullname
*/}}
{{- define "volundr.web.fullname" -}}
{{- printf "%s-web" (include "volundr.fullname" .) }}
{{- end }}

{{/*
Web component labels
*/}}
{{- define "volundr.web.labels" -}}
helm.sh/chart: {{ include "volundr.chart" . }}
{{ include "volundr.web.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: web
app.kubernetes.io/part-of: volundr
{{- end }}

{{/*
Web component selector labels
*/}}
{{- define "volundr.web.selectorLabels" -}}
app.kubernetes.io/name: {{ include "volundr.name" . }}-web
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Web component image (global overrides local)
*/}}
{{- define "volundr.web.image" -}}
{{- $registryName := .Values.web.image.registry -}}
{{- $repositoryName := .Values.web.image.repository -}}
{{- $tag := .Values.web.image.tag | default .Chart.AppVersion -}}
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
Web Next component fullname
*/}}
{{- define "volundr.webNext.fullname" -}}
{{- printf "%s-web-next" (include "volundr.fullname" .) }}
{{- end }}

{{/*
Web Next component labels
*/}}
{{- define "volundr.webNext.labels" -}}
helm.sh/chart: {{ include "volundr.chart" . }}
{{ include "volundr.webNext.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: web-next
app.kubernetes.io/part-of: volundr
{{- end }}

{{/*
Web Next component selector labels
*/}}
{{- define "volundr.webNext.selectorLabels" -}}
app.kubernetes.io/name: {{ include "volundr.name" . }}-web-next
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Web Next component image (global overrides local)
*/}}
{{- define "volundr.webNext.image" -}}
{{- $registryName := .Values.webNext.image.registry -}}
{{- $repositoryName := .Values.webNext.image.repository -}}
{{- $tag := .Values.webNext.image.tag | default .Chart.AppVersion -}}
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
Return the domain (global overrides local)
*/}}
{{- define "volundr.domain" -}}
{{- if and .Values.global .Values.global.domain -}}
  {{- .Values.global.domain -}}
{{- else -}}
  {{- .Values.domain | default "example.com" -}}
{{- end -}}
{{- end }}

{{/*
Return the session gateway name (defaults to fullname-gateway)
*/}}
{{- define "volundr.sessionGateway.name" -}}
{{- .Values.sessionGateway.name | default (printf "%s-gateway" (include "volundr.fullname" .)) -}}
{{- end }}

{{/*
Return the session gateway hostname (defaults to sessions.{domain})
*/}}
{{- define "volundr.sessionGateway.hostname" -}}
{{- if .Values.sessionGateway.hostname -}}
  {{- .Values.sessionGateway.hostname -}}
{{- else -}}
  {{- printf "sessions.%s" (include "volundr.domain" .) -}}
{{- end -}}
{{- end }}
