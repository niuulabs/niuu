{{- /*
The `mesh:` section of the broker's config file, shared by the skuld and skuld-planner charts
(this file is symlinked into the planner's templates, so the two cannot drift). Every key of
Skuld's `mesh` settings (skuld.config.MeshConfig) is a value of the chart; a key left null
keeps Skuld's own default.
*/ -}}
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
  {{- if or .Values.mesh.nng.pubSubAddress .Values.mesh.nng.reqRepAddress }}
  nng:
    {{- if .Values.mesh.nng.pubSubAddress }}
    pub_sub_address: {{ .Values.mesh.nng.pubSubAddress | toJson }}
    {{- end }}
    {{- if .Values.mesh.nng.reqRepAddress }}
    req_rep_address: {{ .Values.mesh.nng.reqRepAddress | toJson }}
    {{- end }}
  {{- end }}
  {{- if kindIs "slice" .Values.mesh.adapters }}
  adapters: {{ .Values.mesh.adapters | toJson }}
  {{- end }}
  {{- if kindIs "slice" .Values.mesh.discoveryAdapters }}
  discovery_adapters: {{ .Values.mesh.discoveryAdapters | toJson }}
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
