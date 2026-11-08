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
Whether the Envoy sidecar fronts the broker.
*/}}
{{- define "skuld.envoyEnabled" -}}
{{- if .Values.envoy.enabled -}}
true
{{- end -}}
{{- end }}

{{/*
Whether the JWT + ext_authz ownership gate fronts the broker (the secure
profile).
*/}}
{{- define "skuld.authEnforced" -}}
{{- if and .Values.wsAuth.enforce_ownership .Values.envoy.enabled -}}
true
{{- end -}}
{{- end }}

{{/*
Route id: the /s/<routeId> path segment on the shared gateway. It is the
session id.
*/}}
{{- define "skuld.routeId" -}}
{{- .Values.session.id -}}
{{- end }}

{{/*
Effective session id: session.id.
*/}}
{{- define "skuld.sessionId" -}}
{{- include "skuld.routeId" . -}}
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

{{/* Git and GitHub CLI credentials for session runtimes. */}}
{{- define "skuld.gitCredentialEnv" -}}
{{- if .Values.git.credentials.tokenFile -}}
- name: GIT_USERNAME
  value: {{ .Values.git.credentials.username | default "x-access-token" | quote }}
- name: GIT_TOKEN_FILE
  value: {{ .Values.git.credentials.tokenFile | quote }}
- name: GIT_CONFIG_COUNT
  value: "1"
- name: GIT_CONFIG_KEY_0
  value: {{ printf "credential.%s.helper" .Values.git.repoUrl | quote }}
- name: GIT_CONFIG_VALUE_0
  value: '!f() { [ "$1" = get ] || return 0; token=$(cat "$GIT_TOKEN_FILE") || return 1; [ -n "$token" ] || return 1; printf "username=%s\npassword=%s\n" "$GIT_USERNAME" "$token"; }; f'
{{- else if .Values.git.credentials.secretName -}}
- name: GIT_USERNAME
  value: {{ .Values.git.credentials.username | default "x-access-token" | quote }}
- name: GIT_CONFIG_COUNT
  value: "1"
- name: GIT_CONFIG_KEY_0
  value: credential.https://github.com.helper
- name: GIT_CONFIG_VALUE_0
  value: '!f() { [ "$1" = get ] || return 0; printf "username=%s\npassword=%s\n" "$GIT_USERNAME" "$GIT_TOKEN"; }; f'
{{- range list "GIT_TOKEN" "GITHUB_TOKEN" "GH_TOKEN" }}
- name: {{ . }}
  valueFrom:
    secretKeyRef:
      name: {{ $.Values.git.credentials.secretName }}
      key: {{ $.Values.git.credentials.tokenKey | default "token" }}
{{- end }}
{{- end }}
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

{{/* User-owned scratch: temporary files are isolated; caches survive new sessions. */}}
{{- define "skuld.scratchMounts" -}}
{{- if and .root.Values.homeVolume.enabled .root.Values.homeVolume.persistentTmp }}
- name: home
  mountPath: /tmp
  subPath: {{ printf "tmp/sessions/%s/%s" (include "skuld.sessionId" .root) .container | quote }}
- name: home
  mountPath: /var/cache/niuu
  subPath: tmp/cache
{{- end }}
{{- end }}

{{- define "skuld.scratchEnv" -}}
{{- if and .Values.homeVolume.enabled .Values.homeVolume.persistentTmp }}
- name: TMPDIR
  value: /tmp
- name: XDG_CACHE_HOME
  value: /var/cache/niuu
- name: GOCACHE
  value: /var/cache/niuu/go-build
- name: GOMODCACHE
  value: /var/cache/niuu/go-mod
- name: npm_config_cache
  value: /var/cache/niuu/npm
- name: PIP_CACHE_DIR
  value: /var/cache/niuu/pip
- name: UV_CACHE_DIR
  value: /var/cache/niuu/uv
{{- end }}
{{- end }}

{{/*
Pod-local Cedar ext_authz sidecar: every request Envoy admits is first checked
against the session's owner and tenant.
*/}}
{{- define "skuld.cedarAuthzContainer" -}}
- name: cedar-authz
  image: {{ include "skuld.image" . | quote }}
  imagePullPolicy: {{ .Values.image.pullPolicy }}
  command: ["python", "-m", "identity.authz_main"]
  args: ["--config", "/etc/authorization/authz.yaml"]
  readinessProbe:
    exec:
      command:
        - python
        - -c
        - "import grpc; from envoy.service.auth.v3 import external_auth_pb2 as pb, external_auth_pb2_grpc as rpc; channel = grpc.insecure_channel('127.0.0.1:{{ .Values.envoy.authorization.port }}'); assert rpc.AuthorizationStub(channel).Check(pb.CheckRequest(), timeout={{ .Values.envoy.authorization.readinessProbe.timeoutSeconds }}).denied_response.status.code == 401"
    periodSeconds: {{ .Values.envoy.authorization.readinessProbe.periodSeconds }}
    timeoutSeconds: {{ .Values.envoy.authorization.readinessProbe.timeoutSeconds }}
    failureThreshold: {{ .Values.envoy.authorization.readinessProbe.failureThreshold }}
  securityContext:
    runAsNonRoot: true
    runAsUser: {{ .Values.securityContext.runAsUser }}
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    capabilities:
      drop: [ALL]
  resources:
    {{- toYaml .Values.envoy.authorization.resources | nindent 4 }}
  volumeMounts:
    - name: authz-config
      mountPath: /etc/authorization
      readOnly: true
    {{- with .Values.envoy.authorization.extraVolumeMounts }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
{{- end }}

{{/*
Envoy sidecar: JWT validation and header extraction in front of the broker.
Shared by the session pod and the room gateway pod.
*/}}
{{- define "skuld.envoyContainer" -}}
# Envoy sidecar - JWT validation and header extraction
- name: envoy
  image: "{{ .Values.envoy.image.repository }}:{{ .Values.envoy.image.tag }}"
  imagePullPolicy: {{ .Values.envoy.image.pullPolicy }}
  securityContext:
    runAsUser: {{ .Values.securityContext.runAsUser }}
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
  ports:
    - name: envoy
      containerPort: {{ .Values.envoy.port }}
      protocol: TCP
  {{- with .Values.envoy.resources }}
  resources:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  volumeMounts:
    - name: envoy-config
      mountPath: /etc/envoy
      readOnly: true
    {{- with .Values.envoy.extraVolumeMounts }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
  readinessProbe:
    tcpSocket:
      port: {{ .Values.envoy.port }}
    initialDelaySeconds: 2
    periodSeconds: 5
  livenessProbe:
    tcpSocket:
      port: {{ .Values.envoy.port }}
    initialDelaySeconds: 5
    periodSeconds: 15
{{- end }}

{{/*
Ports of the pod's own infrastructure, never offered as live previews: nginx,
the broker, Envoy and its admin and authz listeners, the terminal, the Skuld
mesh listeners, plus preview.excludedPorts.
*/}}
{{- define "skuld.previewExcludedPorts" -}}
{{- $ports := list (int .Values.service.port) 8080 8081 (int .Values.envoy.port) (int .Values.envoy.adminPort) (int .Values.envoy.authorization.port) (int .Values.localServices.terminal.port) 7480 7481 7580 -}}
{{- range .Values.preview.excludedPorts }}{{ $ports = append $ports (int .) }}{{ end -}}
{{- $ports | uniq | toJson -}}
{{- end }}
