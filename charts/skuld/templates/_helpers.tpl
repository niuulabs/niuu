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
Resident workload-token exchange: always the platform's
(resident.platform.workloadExchangeUrl, else resident.platform.baseUrl). The
cluster's own Volundr (volundr.apiUrl) serves the resident's cluster-local
calls; it is not where the resident's identity is exchanged.
*/}}
{{- define "skuld.residentWorkloadExchangeUrl" -}}
{{- if .Values.resident.platform.workloadExchangeUrl -}}
{{- .Values.resident.platform.workloadExchangeUrl -}}
{{- else -}}
{{- printf "%s/api/v1/tokens/workload/exchange" (required "resident.platform.baseUrl is required when resident.platform.enabled=true" .Values.resident.platform.baseUrl | trimSuffix "/") -}}
{{- end -}}
{{- end }}

{{/*
Resident display name (defaults to the persona).
*/}}
{{- define "skuld.residentName" -}}
{{- .Values.resident.name | default (include "skuld.residentPersona" .) -}}
{{- end }}

{{/*
Whether this release runs as a room gateway: only the broker, as an
authenticated collaboration room for a Ravn resident that runs elsewhere
(guarded so partial values files that override `roomGateway` keep it disabled).
*/}}
{{- define "skuld.roomGatewayEnabled" -}}
{{- if and .Values.roomGateway .Values.roomGateway.enabled -}}
true
{{- end -}}
{{- end }}

{{/*
Whether the Envoy sidecar fronts the broker: configured explicitly, or implied
by room gateway mode, where an authenticated Envoy is the only way in.
*/}}
{{- define "skuld.envoyEnabled" -}}
{{- if or .Values.envoy.enabled (include "skuld.roomGatewayEnabled" .) -}}
true
{{- end -}}
{{- end }}

{{/*
Whether the JWT + ext_authz ownership gate fronts the broker (the secure
profile, or room gateway mode, where it is mandatory).
*/}}
{{- define "skuld.authEnforced" -}}
{{- if or (include "skuld.roomGatewayEnabled" .) (and .Values.wsAuth.enforce_ownership .Values.envoy.enabled) -}}
true
{{- end -}}
{{- end }}

{{/*
Route id: the /s/<routeId> path segment on the shared gateway. For residents
it defaults to "<namespace>-<release>" so releases never clash; for ordinary
sessions it is the session id; for a room gateway it is roomGateway.routeId
(the /rooms/<routeId> path segment), required and validated.
*/}}
{{- define "skuld.routeId" -}}
{{- if include "skuld.roomGatewayEnabled" . -}}
{{- required "roomGateway.routeId is required when roomGateway.enabled=true: it is the path segment (/rooms/<routeId>) clients use and the default session id; set it to a short lowercase DNS-style name such as the resident's name" .Values.roomGateway.routeId -}}
{{- else if include "skuld.residentEnabled" . -}}
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
against the session's owner and tenant. Shared by the session pod and the room
gateway pod.
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
Public path prefix of a room gateway behind a shared host: /rooms/<routeId>.
Empty when the room owns its host (roomGateway.ingress.mode: host).
*/}}
{{- define "skuld.roomGatewayPathPrefix" -}}
{{- if eq (.Values.roomGateway.ingress.mode | default "path") "path" -}}
{{- printf "%s/%s" (.Values.roomGateway.ingress.pathPrefix | trimSuffix "/") (include "skuld.routeId" .) -}}
{{- end -}}
{{- end }}

{{/*
Room gateway validation. Renders nothing; fails the render, with the remedy,
for any configuration that would leave the room reachable without
authentication, or that asks for something this mode cannot provide. Included
by every template room gateway mode changes, so the remedy is what the
operator sees whichever template Helm renders first.
*/}}
{{- define "skuld.roomGatewayValidate" -}}
{{- if include "skuld.roomGatewayEnabled" . -}}
{{- $rg := .Values.roomGateway -}}
{{- $routeId := include "skuld.routeId" . -}}
{{- if not (regexMatch "^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$" $routeId) -}}
{{- fail (printf "roomGateway.routeId %q must be a lowercase DNS-style label (letters, digits and '-', at most 63 characters): it is a URL path segment and the default session id" $routeId) -}}
{{- end -}}
{{- $_ := required "roomGateway.room.environmentId is required when roomGateway.enabled=true: set it to the environment id the resident's Ravn runs in (its environment.id), so the room and the Ravn share one environment" $rg.room.environmentId -}}
{{- $_ := required "roomGateway.room.defaultTargetPeerId is required when roomGateway.enabled=true: set it to the peer id of the resident that connects to /ws/ravn/<peer> (for example regin), so untargeted chat reaches it" $rg.room.defaultTargetPeerId -}}
{{- $_ := required "session.ownerId is required when roomGateway.enabled=true: set it to the operator's IDP subject (the 'sub' of their Keycloak token), which the resident's workload token must carry too" .Values.session.ownerId -}}
{{- $_ := required "session.tenantId is required when roomGateway.enabled=true: set it to the operator's tenant (their token's tenant_id claim)" .Values.session.tenantId -}}
{{- $jwt := .Values.envoy.jwt -}}
{{- if not $jwt.enabled -}}
{{- fail "roomGateway.enabled=true requires envoy.jwt.enabled=true: a room gateway is only reachable through the JWT-verifying Envoy sidecar. Set envoy.jwt.enabled, envoy.jwt.issuer, envoy.jwt.audiences, envoy.jwt.jwksUri (https), envoy.jwt.keycloakHost and envoy.jwt.keycloakTls=true for the operator's IDP, and envoy.jwt.workload.* for the resident's platform workload token" -}}
{{- end -}}
{{- if not (and $jwt.issuer $jwt.audiences $jwt.keycloakHost $jwt.keycloakTls (hasPrefix "https://" $jwt.jwksUri)) -}}
{{- fail "roomGateway.enabled=true requires envoy.jwt.issuer, envoy.jwt.audiences, envoy.jwt.keycloakHost, envoy.jwt.keycloakTls=true and an https envoy.jwt.jwksUri: operator tokens (Lexi, the web UI) are verified against that IDP's keys and the room does not start without them" -}}
{{- end -}}
{{- if and $jwt.workload.enabled (not (and $jwt.workload.issuer $jwt.workload.audiences $jwt.workload.jwksHost $jwt.workload.jwksTls (hasPrefix "https://" $jwt.workload.jwksUri))) -}}
{{- fail "envoy.jwt.workload.enabled=true requires envoy.jwt.workload.issuer, envoy.jwt.workload.audiences, envoy.jwt.workload.jwksHost, envoy.jwt.workload.jwksTls=true and an https envoy.jwt.workload.jwksUri: the resident's workload token is verified against the platform issuer's keys" -}}
{{- end -}}
{{- if .Values.wsAuth.allow_loopback -}}
{{- fail "roomGateway.enabled=true requires wsAuth.allow_loopback=false: nothing in a room gateway pod may connect to the broker without a verified identity. Remove wsAuth.allow_loopback (values-auth.yaml sets it for session pods; do not apply it here)" -}}
{{- end -}}
{{- if ne (.Values.wsAuth.room_role_source | default "deployment") "deployment" -}}
{{- fail "roomGateway.enabled=true requires wsAuth.room_role_source=deployment: the room's owner and tenant admins are admitted by the pod-local ext_authz check, which runs before any per-participant room-role lookup" -}}
{{- end -}}
{{- if .Values.gateway.enabled -}}
{{- fail "roomGateway.enabled=true cannot be combined with gateway.enabled=true: a room gateway is exposed by roomGateway.ingress (or its Service), not by a Gateway API HTTPRoute. Set gateway.enabled=false" -}}
{{- end -}}
{{- if include "skuld.residentEnabled" . -}}
{{- fail "roomGateway.enabled=true cannot be combined with resident.enabled=true: a room gateway hosts the room for a resident that runs elsewhere. Disable one of them" -}}
{{- end -}}
{{- if gt (int .Values.replicaCount) 1 -}}
{{- fail "roomGateway.enabled=true requires replicaCount of 0 or 1: the room's participants and history live in one broker process, so a second replica would split the room" -}}
{{- end -}}
{{- if not (hasPrefix "/" $rg.dataPath) -}}
{{- fail (printf "roomGateway.dataPath %q must be an absolute path" $rg.dataPath) -}}
{{- end -}}
{{- $tg := $rg.telegram -}}
{{- if $tg.enabled -}}
{{- $_ := required "roomGateway.telegram.secretName is required when roomGateway.telegram.enabled=true: name a Secret holding the bot token and chat id (keys roomGateway.telegram.botTokenKey and roomGateway.telegram.chatIdKey); they are never rendered into the ConfigMap" $tg.secretName -}}
{{- if not (has $tg.topicMode (list "shared_chat" "fixed_topic" "topic_per_session")) -}}
{{- fail (printf "roomGateway.telegram.topicMode %q must be one of shared_chat, fixed_topic, topic_per_session" $tg.topicMode) -}}
{{- end -}}
{{- if and (eq $tg.topicMode "fixed_topic") (kindIs "invalid" $tg.messageThreadId) -}}
{{- fail "roomGateway.telegram.topicMode=fixed_topic requires roomGateway.telegram.messageThreadId: set the topic's thread id, or use topicMode shared_chat" -}}
{{- end -}}
{{- end -}}
{{- $ing := $rg.ingress -}}
{{- if $ing.enabled -}}
{{- if not (has ($ing.mode | default "path") (list "path" "host")) -}}
{{- fail (printf "roomGateway.ingress.mode %q must be path (shared host, /rooms/<routeId>) or host (dedicated host)" $ing.mode) -}}
{{- end -}}
{{- $_ := required "roomGateway.ingress.host is required when roomGateway.ingress.enabled=true: set the public host (for example the shared host many rooms use), or set roomGateway.ingress.enabled=false to expose the room through its Service only" $ing.host -}}
{{- if and (eq ($ing.mode | default "path") "path") (not (regexMatch "^/[A-Za-z0-9._~/-]*[A-Za-z0-9._~-]$" $ing.pathPrefix)) -}}
{{- fail (printf "roomGateway.ingress.pathPrefix %q must start with '/' and not end with '/' (for example /rooms)" $ing.pathPrefix) -}}
{{- end -}}
{{- if $ing.tls.enabled -}}
{{- $_ := required "roomGateway.ingress.tls.secretName is required when roomGateway.ingress.tls.enabled=true: name the TLS Secret for the host (an existing shared one is fine); set roomGateway.ingress.tls.enabled=false only behind a TLS-terminating proxy" $ing.tls.secretName -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end }}
