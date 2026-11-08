{{- /*
The `mesh:` section of the broker's config file, shared by the skuld and skuld-planner charts
(this file is symlinked into the planner's templates, so the two cannot drift). Every key of
Skuld's `mesh` settings (skuld.config.MeshConfig) is a value of the chart; a key left null
keeps Skuld's own default.
*/ -}}
{{- /*
"true" when the operator only enabled the mesh on nng and named no discovery: the sessions are
pods of one cluster, so they find each other by listing the pods of their realm
(`k8s` discovery) and dial the PUB socket each pod opens on its own IP. The chart then also
gives the pod its IP, its realm label and its peer id (`skuld.meshPodLabels`, `skuld.meshEnv`).
*/ -}}
{{- define "skuld.meshK8sDiscovery" -}}
{{- if and .Values.mesh.discoverPods .Values.mesh.enabled (eq (.Values.mesh.transport | default "nng") "nng") (not .Values.mesh.discoveryAdapters) (not .Values.mesh.adapters) -}}true{{- end -}}
{{- end }}

{{- /*
"true" when this release's sessions list pods to find their peers in the release namespace: the
cluster default above, or a discovery entry the operator wrote that names `k8s` and this
namespace. The chart then gives the session's ServiceAccount a namespaced Role that can
get, list and watch pods and nothing else (mesh-rbac.yaml). A `k8s` entry for another
namespace, or for every namespace, is the operator's to grant: nothing here is cluster-wide.
`mesh.rbac.create: false` leaves the grant to the operator.
*/ -}}
{{- define "skuld.meshUsesK8sDiscovery" -}}
{{- if and .Values.mesh.enabled .Values.mesh.rbac.create -}}
{{- if include "skuld.meshK8sDiscovery" . -}}
true
{{- else -}}
{{- range (.Values.mesh.discoveryAdapters | default list) -}}
{{- if and (kindIs "map" .) (eq (.adapter | default "") "k8s") (eq (.namespace | default "") $.Release.Namespace) -}}
true
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end }}

{{- define "skuld.meshNngPort" -}}
{{- .Values.mesh.nng.port | default 7480 -}}
{{- end }}

{{- /* Labels and annotations the k8s discovery of the other sessions reads. */ -}}
{{- define "skuld.meshPodLabels" -}}
ravn.niuu.world/realm: {{ .Values.mesh.realmId | quote }}
ravn.niuu.world/role: agent
{{- end }}

{{- /* The session is a Skuld participant, addressed by its name (niuu k8s discovery). */ -}}
{{- define "skuld.meshPodAnnotations" -}}
ravn.niuu.world/peer-id: {{ .Values.mesh.peerId | default .Values.session.id | quote }}
ravn.niuu.world/participant-type: "skuld"
{{- with .Values.session.name }}
ravn.niuu.world/display-name: {{ . | quote }}
{{- end }}
{{- end }}

{{- /* The pod's own IP, and the host other pods dial this one at. */ -}}
{{- define "skuld.meshEnv" -}}
- name: POD_IP
  valueFrom:
    fieldRef:
      fieldPath: status.podIP
{{- if not .Values.mesh.nng.advertiseHost }}
- name: SKULD__MESH__NNG__ADVERTISE_HOST
  value: "$(POD_IP)"
{{- end }}
{{- end }}

{{- define "skuld.meshConfig" -}}
# The mesh: this session as a peer of a flock.
mesh:
  enabled: {{ .Values.mesh.enabled | toJson }}
  transport: {{ .Values.mesh.transport | toJson }}
  {{- if .Values.mesh.peerId }}
  peer_id: {{ .Values.mesh.peerId | toJson }}
  {{- end }}
  {{- if .Values.mesh.realmId }}
  realm_id: {{ .Values.mesh.realmId | toJson }}
  {{- end }}
  {{- if not (kindIs "invalid" .Values.mesh.persona) }}
  persona: {{ .Values.mesh.persona | toJson }}
  {{- end }}
  {{- if .Values.mesh.role }}
  role: {{ .Values.mesh.role | toJson }}
  {{- end }}
  {{- if .Values.mesh.description }}
  description: {{ .Values.mesh.description | toJson }}
  {{- end }}
  {{- if kindIs "slice" .Values.mesh.capabilities }}
  capabilities: {{ .Values.mesh.capabilities | toJson }}
  {{- end }}
  {{- if kindIs "slice" .Values.mesh.tools }}
  tools: {{ .Values.mesh.tools | toJson }}
  {{- end }}
  {{- if kindIs "slice" .Values.mesh.consumesEventTypes }}
  consumes_event_types: {{ .Values.mesh.consumesEventTypes | toJson }}
  {{- end }}
  {{- if kindIs "slice" .Values.mesh.publishEventTypes }}
  publish_event_types: {{ .Values.mesh.publishEventTypes | toJson }}
  {{- end }}
  {{- if not (kindIs "invalid" .Values.mesh.publishedEventUrgency) }}
  published_event_urgency: {{ .Values.mesh.publishedEventUrgency | toJson }}
  {{- end }}
  {{- $k8s := include "skuld.meshK8sDiscovery" . }}
  {{- if or .Values.mesh.nng.pubSubAddress .Values.mesh.nng.reqRepAddress .Values.mesh.nng.advertiseHost .Values.mesh.nng.rediscoverIntervalS $k8s }}
  nng:
    {{- if .Values.mesh.nng.pubSubAddress }}
    pub_sub_address: {{ .Values.mesh.nng.pubSubAddress | toJson }}
    {{- else if $k8s }}
    pub_sub_address: {{ printf "tcp://0.0.0.0:%v" (include "skuld.meshNngPort" .) | toJson }}
    {{- end }}
    {{- if .Values.mesh.nng.reqRepAddress }}
    req_rep_address: {{ .Values.mesh.nng.reqRepAddress | toJson }}
    {{- end }}
    {{- if .Values.mesh.nng.advertiseHost }}
    advertise_host: {{ .Values.mesh.nng.advertiseHost | toJson }}
    {{- end }}
    {{- if .Values.mesh.nng.rediscoverIntervalS }}
    rediscover_interval_s: {{ .Values.mesh.nng.rediscoverIntervalS | toJson }}
    {{- end }}
  {{- end }}
  {{- if kindIs "slice" .Values.mesh.adapters }}
  adapters: {{ .Values.mesh.adapters | toJson }}
  {{- end }}
  {{- if kindIs "slice" .Values.mesh.discoveryAdapters }}
  discovery_adapters: {{ .Values.mesh.discoveryAdapters | toJson }}
  {{- else if $k8s }}
  discovery_adapters: {{ list (dict "adapter" "k8s" "namespace" .Release.Namespace "nng_port" (int (include "skuld.meshNngPort" .))) | toJson }}
  {{- end }}
  {{- if .Values.mesh.nats }}
  nats: {{ .Values.mesh.nats | toJson }}
  {{- end }}
  {{- if not (kindIs "invalid" .Values.mesh.rpcTimeoutS) }}
  rpc_timeout_s: {{ .Values.mesh.rpcTimeoutS | toJson }}
  {{- end }}
  {{- if not (kindIs "invalid" .Values.mesh.rpcReplyCacheSize) }}
  rpc_reply_cache_size: {{ .Values.mesh.rpcReplyCacheSize | toJson }}
  {{- end }}
  {{- if not (kindIs "invalid" .Values.mesh.diffMaxBytes) }}
  diff_max_bytes: {{ .Values.mesh.diffMaxBytes | toJson }}
  {{- end }}
  {{- if not (kindIs "invalid" .Values.mesh.diffTimeoutS) }}
  diff_timeout_s: {{ .Values.mesh.diffTimeoutS | toJson }}
  {{- end }}
  {{- $attestation := dict }}
  {{- range $key, $value := (.Values.mesh.attestation | default dict) }}
  {{- if not (kindIs "invalid" $value) }}
  {{- $_ := set $attestation $key $value }}
  {{- end }}
  {{- end }}
  {{- if $attestation }}
  # Proof of who sent each mesh message: needs no setting; only what is set is rendered.
  attestation:
    {{- if not (kindIs "invalid" $attestation.required) }}
    required: {{ $attestation.required | toJson }}
    {{- end }}
    {{- if not (kindIs "invalid" $attestation.renewFraction) }}
    renew_fraction: {{ $attestation.renewFraction | toJson }}
    {{- end }}
    {{- if not (kindIs "invalid" $attestation.retryFraction) }}
    retry_fraction: {{ $attestation.retryFraction | toJson }}
    {{- end }}
    {{- if not (kindIs "invalid" $attestation.clockSkewS) }}
    clock_skew_s: {{ $attestation.clockSkewS | toJson }}
    {{- end }}
    {{- if not (kindIs "invalid" $attestation.eventMaxAgeS) }}
    event_max_age_s: {{ $attestation.eventMaxAgeS | toJson }}
    {{- end }}
  {{- end }}
{{- end }}
