{{/*
Expand the name of the chart.
*/}}
{{- define "ravn.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "ravn.fullname" -}}
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
{{- define "ravn.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Niuu deployment discovery labels.
*/}}
{{- define "ravn.niuuLabels" -}}
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
{{- define "ravn.labels" -}}
helm.sh/chart: {{ include "ravn.chart" . }}
{{ include "ravn.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{ include "ravn.niuuLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: ravn-api
app.kubernetes.io/part-of: niuu
{{- end }}

{{/*
Selector labels
*/}}
{{- define "ravn.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ravn.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "ravn.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "ravn.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Return the proper image name (explicit local tag overrides global)
*/}}
{{- define "ravn.image" -}}
{{- $registryName := .Values.image.registry -}}
{{- $repositoryName := .Values.image.repository -}}
{{- $tag := .Values.image.tag -}}
{{- if and .Values.global .Values.global.image -}}
  {{- if .Values.global.image.registry -}}
    {{- $registryName = .Values.global.image.registry -}}
  {{- end -}}
  {{- if and (not $tag) .Values.global.image.tag -}}
    {{- $tag = .Values.global.image.tag -}}
  {{- end -}}
{{- end -}}
{{- if not $tag -}}
  {{- $tag = .Chart.AppVersion -}}
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
{{- define "ravn.imagePullSecrets" -}}
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
Whether the config.yaml ConfigMap is rendered at all: either the operator
supplied config.settings directly, or triggerStore.enabled/budgetLedger
.enabled resolved to an adapter (configmap.yaml then synthesizes it into
config.yaml even with no other settings given). Not database.enabled/
persistence.enabled directly — those are general-purpose flags a release
may set for reasons unrelated to this feature (see ravn.triggerStoreAdapter).
*/}}
{{- define "ravn.configEnabled" -}}
{{- $triggerAdapter := include "ravn.triggerStoreAdapter" . -}}
{{- $budgetAdapter := include "ravn.budgetLedgerAdapter" . -}}
{{- if or .Values.config.settings $triggerAdapter $budgetAdapter -}}
true
{{- end -}}
{{- end }}

{{/*
The trigger_store/budget_ledger adapter this chart will actually render into
config.yaml — the single source of truth shared by configmap.yaml (which
renders it) and ravn.validatePersistence below (which checks it has its
prerequisite). Priority:
  1. An operator override left directly in config.settings.trigger_store
     .adapter / config.settings.budget_ledger.adapter — an explicit,
     self-contained opt-in on its own (dynamic-adapters.md), independent of
     the flags below.
  2. triggerStore.enabled / budgetLedger.enabled (default false) — the
     feature-specific opt-in. database.enabled/persistence.enabled are
     general-purpose flags that predate this feature (persistence already
     backs the cron job store and a sqlite memory backend) and must not by
     themselves turn this on for a release that set them for something
     else; database.enabled takes precedence over persistence.enabled when
     both are set.
  3. Neither set: empty — this Ravn API has no store, matching every
     already-deployed release before this feature existed.
*/}}
{{- define "ravn.triggerStoreAdapter" -}}
{{- $override := dig "trigger_store" "adapter" "" (.Values.config.settings | default dict) -}}
{{- if $override -}}
{{- $override -}}
{{- else if .Values.triggerStore.enabled -}}
{{- if .Values.database.enabled -}}
ravn.adapters.trigger_store.LazyPostgresTriggerStore
{{- else if .Values.persistence.enabled -}}
ravn.adapters.trigger_store.FileTriggerStore
{{- end -}}
{{- end -}}
{{- end }}

{{- define "ravn.budgetLedgerAdapter" -}}
{{- $override := dig "budget_ledger" "adapter" "" (.Values.config.settings | default dict) -}}
{{- if $override -}}
{{- $override -}}
{{- else if .Values.budgetLedger.enabled -}}
{{- if .Values.database.enabled -}}
ravn.adapters.budget_ledger.LazyPostgresBudgetLedger
{{- else if .Values.persistence.enabled -}}
ravn.adapters.budget_ledger.FileBudgetLedger
{{- end -}}
{{- end -}}
{{- end }}

{{- define "ravn.validatePersistence" -}}
{{/*
trigger_store/budget_ledger are opt-in (empty adapter by default — see
ravn.config.TriggerStoreConfig/BudgetLedgerConfig): with no store selected
at all, this chart renders neither, and /api/v1/ravn/triggers and
/api/v1/ravn/budget/* return 503 — a real, valid, already-deployed state,
not an error. This guard therefore only fires when a store IS selected: an
operator naming the adapter class directly in config.settings, or
triggerStore.enabled/budgetLedger.enabled without a backing store
(database.enabled or persistence.enabled), or a resolved file-backed store
without persistence.enabled — it writes into the container filesystem and
loses everything on the next restart or reschedule — or a resolved Postgres
adapter without database.enabled, or a file-backed store running more than
one replica, since each replica would keep its own file and see a
different, incomplete trigger/budget set. database.enabled has no replica
constraint (Postgres is already shared) — database.existingSecret being
required is enforced separately, in configmap.yaml.
*/}}
{{- if and .Values.triggerStore.enabled (not .Values.database.enabled) (not .Values.persistence.enabled) -}}
{{ fail "ravn: triggerStore.enabled=true requires database.enabled=true or persistence.enabled=true to back it" }}
{{- end -}}
{{- if and .Values.budgetLedger.enabled (not .Values.database.enabled) (not .Values.persistence.enabled) -}}
{{ fail "ravn: budgetLedger.enabled=true requires database.enabled=true or persistence.enabled=true to back it" }}
{{- end -}}
{{- $triggerAdapter := include "ravn.triggerStoreAdapter" . -}}
{{- $budgetAdapter := include "ravn.budgetLedgerAdapter" . -}}
{{- $usesFileStore := or (eq $triggerAdapter "ravn.adapters.trigger_store.FileTriggerStore") (eq $budgetAdapter "ravn.adapters.budget_ledger.FileBudgetLedger") -}}
{{- $usesPostgresStore := or (eq $triggerAdapter "ravn.adapters.trigger_store.LazyPostgresTriggerStore") (eq $budgetAdapter "ravn.adapters.budget_ledger.LazyPostgresBudgetLedger") -}}
{{- if and $usesFileStore (not .Values.persistence.enabled) -}}
{{ fail "ravn: a file-backed trigger_store/budget_ledger adapter is configured without persistence.enabled=true — it would write into the container filesystem and lose all data on the next restart or reschedule" }}
{{- end -}}
{{- if and $usesPostgresStore (not .Values.database.enabled) -}}
{{ fail "ravn: a Postgres trigger_store/budget_ledger adapter is configured without database.enabled=true" }}
{{- end -}}
{{- if and $usesFileStore (or .Values.autoscaling.enabled (gt (int .Values.replicaCount) 1)) -}}
{{ fail "ravn: autoscaling.enabled or replicaCount>1 with a file-backed trigger_store/budget_ledger — several replicas would each keep their own file and see a different, incomplete set; set database.enabled=true instead" }}
{{- end -}}
{{- end }}

{{/*
Annotations for checksum/config - forces restart on config changes
*/}}
{{- define "ravn.checksumAnnotations" -}}
{{- if .Values.envoy.enabled }}
checksum/envoy: {{ include (print $.Template.BasePath "/envoy-configmap.yaml") . | sha256sum }}
{{- end }}
{{- if include "ravn.configEnabled" . }}
checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
{{- end }}
{{- end }}
