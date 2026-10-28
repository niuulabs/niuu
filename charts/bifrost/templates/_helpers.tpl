{{/*
Expand the name of the chart.
*/}}
{{- define "bifrost.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "bifrost.fullname" -}}
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
The time a terminating pod needs to drain: new requests keep arriving while the
endpoints are withdrawn (shutdown.drainSeconds), then in-flight requests, streamed
model answers included, finish (config.shutdown_grace_seconds). A grace period
shorter than that makes the kubelet SIGKILL the gateway mid-stream, which is the
failure this drain exists to prevent, so it is a render error rather than a default.
*/}}
{{- define "bifrost.drainSeconds" -}}
{{- int (.Values.shutdown.drainSeconds | default 0) -}}
{{- end }}

{{- define "bifrost.terminationGracePeriodSeconds" -}}
{{- $needed := add (int (include "bifrost.drainSeconds" .)) (int .Values.config.shutdown_grace_seconds) -}}
{{- if le (int .Values.terminationGracePeriodSeconds) $needed -}}
{{- fail (printf "terminationGracePeriodSeconds (%d) must exceed shutdown.drainSeconds + config.shutdown_grace_seconds (%d): the kubelet would SIGKILL the gateway while streamed answers are still in flight. Raise terminationGracePeriodSeconds, or lower config.shutdown_grace_seconds." (int .Values.terminationGracePeriodSeconds) $needed) -}}
{{- end -}}
{{- int .Values.terminationGracePeriodSeconds -}}
{{- end }}

{{/*
preStop for a serving container: keep answering for the given seconds while the
pod is withdrawn from the endpoints.
*/}}
{{- define "bifrost.preStopSleep" -}}
{{- if gt (int .) 0 -}}
lifecycle:
  preStop:
    sleep:
      seconds: {{ int . }}
{{- end -}}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "bifrost.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Niuu deployment discovery labels.
*/}}
{{- define "bifrost.niuuLabels" -}}
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
{{- define "bifrost.labels" -}}
helm.sh/chart: {{ include "bifrost.chart" . }}
{{ include "bifrost.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{ include "bifrost.niuuLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: gateway
app.kubernetes.io/part-of: niuu
{{- end }}

{{/*
Selector labels
*/}}
{{- define "bifrost.selectorLabels" -}}
app.kubernetes.io/name: {{ include "bifrost.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "bifrost.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "bifrost.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Return the proper image name (global overrides local)
*/}}
{{- define "bifrost.image" -}}
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
{{- define "bifrost.imagePullSecrets" -}}
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
Checksum annotations — force pod restarts when config changes
*/}}
{{- define "bifrost.checksumAnnotations" -}}
checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
{{- if .Values.migrations.enabled }}
checksum/migrations: {{ include (print $.Template.BasePath "/migrations-configmap.yaml") . | sha256sum }}
{{- end }}
{{- if .Values.envoy.enabled }}
checksum/envoy: {{ include (print $.Template.BasePath "/envoy-configmap.yaml") . | sha256sum }}
{{- end }}
{{- end }}

{{/*
Whether this Bifröst needs a database at all.

Truthy (a non-empty string) when either the usage store or the audit log is
postgres-backed. A gateway keeping both in process — the chart default — has
nothing to connect to, and demanding credentials it will never use meant it
could not start without a Postgres somebody had to provision first.
*/}}
{{- define "bifrost.needsDatabase" -}}
{{- $usage := (.Values.config.usage_store).adapter | default "memory" -}}
{{- $audit := (.Values.config.audit).adapter | default "null" -}}
{{- if or (eq $usage "postgres") (eq $audit "postgres") }}yes{{ end }}
{{- end }}

{{/*
Return the database secret name.
*/}}
{{- define "bifrost.databaseSecretName" -}}
{{- if .Values.database.existingSecret }}
{{- .Values.database.existingSecret }}
{{- else }}
{{- printf "%s-bifrost-db" .Release.Name }}
{{- end }}
{{- end }}

{{/*
Return the database host.
*/}}
{{- define "bifrost.databaseHost" -}}
{{- if .Values.database.external.enabled }}
{{- .Values.database.external.host }}
{{- else }}
{{- printf "%s-postgresql" .Release.Name }}
{{- end }}
{{- end }}

{{/*
Return the database port.
*/}}
{{- define "bifrost.databasePort" -}}
{{- if .Values.database.external.enabled }}
{{- .Values.database.external.port | default 5432 }}
{{- else }}
{{- 5432 }}
{{- end }}
{{- end }}
