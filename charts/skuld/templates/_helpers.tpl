{{/*
Expand the name of the chart.
*/}}
{{- define "skuld.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "skuld.fullname" -}}
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
{{- define "skuld.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Niuu deployment discovery labels.
*/}}
{{- define "skuld.niuuLabels" -}}
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
Whether this release runs as a resident ravn (guarded so partial values
files that override `resident` with identity-only keys keep it disabled).
*/}}
{{- define "skuld.residentEnabled" -}}
{{- if and .Values.resident .Values.resident.enabled -}}
true
{{- end -}}
{{- end }}

{{/*
Resident persona (required when resident.enabled).
*/}}
{{- define "skuld.residentPersona" -}}
{{- required "resident.persona is required when resident.enabled=true" .Values.resident.persona -}}
{{- end }}

{{/*
Resident display name (defaults to the persona).
*/}}
{{- define "skuld.residentName" -}}
{{- .Values.resident.name | default (include "skuld.residentPersona" .) -}}
{{- end }}

{{/*
Route id: the /s/<routeId> path segment on the shared gateway. For residents
it defaults to "<namespace>-<release>" so releases never clash; for ordinary
sessions it is the session id.
*/}}
{{- define "skuld.routeId" -}}
{{- if include "skuld.residentEnabled" . -}}
{{- .Values.resident.routeId | default (printf "%s-%s" .Release.Namespace .Release.Name) -}}
{{- else -}}
{{- .Values.session.id -}}
{{- end -}}
{{- end }}

{{/*
Effective session id: session.id, falling back to the route id for residents
(where no Volundr session row exists).
*/}}
{{- define "skuld.sessionId" -}}
{{- if .Values.session.id -}}
{{- .Values.session.id -}}
{{- else -}}
{{- include "skuld.routeId" . -}}
{{- end -}}
{{- end }}

{{/*
Common labels
*/}}
{{- define "skuld.labels" -}}
helm.sh/chart: {{ include "skuld.chart" . }}
{{ include "skuld.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{ include "skuld.niuuLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
volundr.io/session-id: {{ include "skuld.sessionId" . | quote }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "skuld.selectorLabels" -}}
app.kubernetes.io/name: {{ include "skuld.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Session workspace path
*/}}
{{- define "skuld.workspacePath" -}}
{{- printf "%s/%s/workspace" .Values.persistence.mountPath (include "skuld.sessionId" .) }}
{{- end }}

{{/*
Return the proper image name (global overrides local)
*/}}
{{- define "skuld.image" -}}
{{- $repository := .Values.image.repository -}}
{{- $tag := .Values.image.tag -}}
{{- if and .Values.global .Values.global.image -}}
  {{- if .Values.global.image.repository -}}
    {{- $repository = .Values.global.image.repository -}}
  {{- end -}}
  {{- if .Values.global.image.tag -}}
    {{- $tag = .Values.global.image.tag -}}
  {{- end -}}
{{- end -}}
{{- printf "%s:%s" $repository $tag -}}
{{- end }}

{{/*
Return image pull secrets (global overrides top-level, converts strings to objects)
*/}}
{{- define "skuld.imagePullSecrets" -}}
{{- $secrets := list -}}
{{- if and .Values.global .Values.global.imagePullSecrets -}}
  {{- $secrets = .Values.global.imagePullSecrets -}}
{{- else if .Values.imagePullSecrets -}}
  {{- $secrets = .Values.imagePullSecrets -}}
{{- end -}}
{{- if $secrets -}}
imagePullSecrets:
  {{- range $secrets }}
  - name: {{ . }}
  {{- end }}
{{- end -}}
{{- end }}
