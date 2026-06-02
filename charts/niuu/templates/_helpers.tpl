{{/*
Expand the name of the chart.
*/}}
{{- define "niuu.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "niuu.fullname" -}}
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
{{- define "niuu.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "niuu.labels" -}}
helm.sh/chart: {{ include "niuu.chart" . }}
{{ include "niuu.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: niuu
{{- end }}

{{/*
Selector labels
*/}}
{{- define "niuu.selectorLabels" -}}
app.kubernetes.io/name: {{ include "niuu.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Resolve a component fullname using the same rules as the child chart.
*/}}
{{- define "niuu.componentFullname" -}}
{{- $root := .root -}}
{{- $name := .name -}}
{{- $values := .values | default dict -}}
{{- if $values.fullnameOverride }}
{{- $values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $componentName := default $name $values.nameOverride }}
{{- if contains $componentName $root.Release.Name }}
{{- $root.Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" $root.Release.Name $componentName | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Resolve a backend service name for the umbrella ingress.
Known service keys point at subchart services; unknown keys are treated
as literal service names to keep the route table extensible.
*/}}
{{- define "niuu.ingressServiceName" -}}
{{- $root := .root -}}
{{- $service := .service -}}
{{- if eq $service "volundr" -}}
{{ include "niuu.componentFullname" (dict "root" $root "name" "volundr" "values" $root.Values.volundr) }}
{{- else if eq $service "forge-api" -}}
{{- if $root.Values.guild.enabled -}}
{{ include "niuu.componentFullname" (dict "root" $root "name" "guild" "values" $root.Values.guild) }}
{{- else -}}
{{ include "niuu.componentFullname" (dict "root" $root "name" "volundr" "values" $root.Values.volundr) }}
{{- end -}}
{{- else if eq $service "volundr-web" -}}
{{ printf "%s-web" (include "niuu.componentFullname" (dict "root" $root "name" "volundr" "values" $root.Values.volundr)) }}
{{- else if eq $service "volundr-web-next" -}}
{{ printf "%s-web-next" (include "niuu.componentFullname" (dict "root" $root "name" "volundr" "values" $root.Values.volundr)) }}
{{- else if eq $service "ting" -}}
{{ include "niuu.componentFullname" (dict "root" $root "name" "ting" "values" $root.Values.ting) }}
{{- else if eq $service "niuu-shared" -}}
{{ include "niuu.componentFullname" (dict "root" $root "name" "niuu-shared" "values" (index $root.Values "niuu-shared")) }}
{{- else if eq $service "guild" -}}
{{ include "niuu.componentFullname" (dict "root" $root "name" "guild" "values" $root.Values.guild) }}
{{- else if eq $service "bifrost" -}}
{{ include "niuu.componentFullname" (dict "root" $root "name" "bifrost" "values" $root.Values.bifrost) }}
{{- else if eq $service "observatory" -}}
{{ include "niuu.componentFullname" (dict "root" $root "name" "observatory" "values" $root.Values.observatory) }}
{{- else if eq $service "mimir-shared" -}}
{{ include "niuu.componentFullname" (dict "root" $root "name" "mimir-shared" "values" (index $root.Values "mimir-shared")) }}
{{- else if eq $service "ravn" -}}
{{ include "niuu.componentFullname" (dict "root" $root "name" "ravn" "values" $root.Values.ravn) }}
{{- else if eq $service "mimir-kanuck" -}}
{{ include "niuu.componentFullname" (dict "root" $root "name" "mimir-kanuck" "values" (index $root.Values "mimir-kanuck")) }}
{{- else if eq $service "mimir-research" -}}
{{ include "niuu.componentFullname" (dict "root" $root "name" "mimir-research" "values" (index $root.Values "mimir-research")) }}
{{- else -}}
{{- $service -}}
{{- end }}
{{- end }}

{{/*
Return whether an ingress backend is enabled for the current umbrella values.
Unknown service names are treated as enabled so custom routes still render.
*/}}
{{- define "niuu.ingressServiceEnabled" -}}
{{- $root := .root -}}
{{- $service := .service -}}
{{- if eq $service "volundr" -}}
{{ ternary "true" "false" ($root.Values.volundr.enabled | default false) }}
{{- else if eq $service "forge-api" -}}
{{ ternary "true" "false" (or ($root.Values.guild.enabled | default false) ($root.Values.volundr.enabled | default false)) }}
{{- else if eq $service "volundr-web" -}}
{{ ternary "true" "false" (and ($root.Values.volundr.enabled | default false) ($root.Values.volundr.web.enabled | default false)) }}
{{- else if eq $service "volundr-web-next" -}}
{{ ternary "true" "false" (and ($root.Values.volundr.enabled | default false) ($root.Values.volundr.webNext.enabled | default false)) }}
{{- else if eq $service "ting" -}}
{{ ternary "true" "false" ($root.Values.ting.enabled | default false) }}
{{- else if eq $service "niuu-shared" -}}
{{ ternary "true" "false" ((index $root.Values "niuu-shared").enabled | default false) }}
{{- else if eq $service "guild" -}}
{{ ternary "true" "false" ($root.Values.guild.enabled | default false) }}
{{- else if eq $service "bifrost" -}}
{{ ternary "true" "false" ($root.Values.bifrost.enabled | default false) }}
{{- else if eq $service "observatory" -}}
{{ ternary "true" "false" ($root.Values.observatory.enabled | default false) }}
{{- else if eq $service "mimir-shared" -}}
{{ ternary "true" "false" ((index $root.Values "mimir-shared").enabled | default false) }}
{{- else if eq $service "ravn" -}}
{{ ternary "true" "false" ($root.Values.ravn.enabled | default false) }}
{{- else if eq $service "mimir-kanuck" -}}
{{ ternary "true" "false" ((index $root.Values "mimir-kanuck").enabled | default false) }}
{{- else if eq $service "mimir-research" -}}
{{ ternary "true" "false" ((index $root.Values "mimir-research").enabled | default false) }}
{{- else -}}
true
{{- end }}
{{- end }}
