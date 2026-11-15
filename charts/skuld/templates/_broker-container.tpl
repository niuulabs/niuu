{{- /*
The broker container of a pod: the session's own (`skuld`), or one member of a unit, rendered from
the member's values (skuld.memberValues) with `containerName`, `configVolume` and `configKey`
set on the context.
*/ -}}
{{- define "skuld.brokerContainer" -}}
# Skuld broker - Claude Code CLI + REST API
- name: {{ .containerName | default "skuld" }}
  image: {{ include "skuld.image" . | quote }}
  imagePullPolicy: {{ .Values.image.pullPolicy }}
  securityContext:
    runAsUser: {{ .Values.securityContext.runAsUser }}
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: false
  ports:
    - name: broker
      containerPort: {{ .Values.brokerPort | default 8081 }}
      protocol: TCP
    {{- range .Values.mesh.peerPorts }}
    - {{- toYaml . | nindent 6 }}
    {{- end }}
    {{- if include "skuld.meshK8sDiscovery" . }}
    - name: mesh-pub
      containerPort: {{ include "skuld.meshNngPort" . }}
      protocol: TCP
    {{- end }}
  env:
    {{- include "skuld.scratchEnv" . | nindent 4 }}
    {{- if include "skuld.meshK8sDiscovery" . }}
    {{- include "skuld.meshEnv" . | nindent 4 }}
    {{- end }}
    - name: SKULD__SESSION__WORKSPACE_DIR
      value: {{ include "skuld.workspacePath" . | quote }}
    {{- if .Values.volundr.apiUrl }}
    - name: SKULD__VOLUNDR_API_URL
      value: {{ .Values.volundr.apiUrl | quote }}
    {{- end }}
    - name: SKULD__SERVICE_USER_ID
      value: {{ .Values.volundr.serviceUserId | default "skuld-broker" | quote }}
    - name: SKULD__SERVICE_TENANT_ID
      value: {{ .Values.volundr.serviceTenantId | default "default" | quote }}
    {{- if .Values.volundr.externalApiTokenSecret.name }}
    - name: SKULD__EXTERNAL_API_TOKEN
      valueFrom:
        secretKeyRef:
          name: {{ .Values.volundr.externalApiTokenSecret.name | quote }}
          key: {{ .Values.volundr.externalApiTokenSecret.key | default "token" | quote }}
    {{- end }}
    {{- if .Values.homeVolume.enabled }}
    - name: HOME
      value: {{ .Values.homeVolume.mountPath | quote }}
    - name: CLAUDE_CONFIG_DIR
      value: "{{ .Values.homeVolume.mountPath }}/{{ .Values.homeVolume.credentialFiles.destDir }}"
    - name: CODEX_HOME
      value: {{ include "skuld.codexHome" . | quote }}
    {{- end }}
    {{- range .Values.envSecrets }}
    - name: {{ .envVar }}
      valueFrom:
        secretKeyRef:
          name: {{ .secretName }}
          key: {{ .secretKey }}
    {{- end }}
    {{- range .Values.envVars }}
    - name: {{ .name }}
      value: {{ .value | quote }}
    {{- end }}
    {{- if .Values.claude.disableAutoUpdate }}
    # The CLI in the image is pinned: a self-update at start-up restarts it
    # inside the session (and fails without write access to the npm prefix).
    - name: DISABLE_AUTOUPDATER
      value: "1"
    {{- end }}
    {{- include "skuld.gitCredentialEnv" . | nindent 4 }}
    {{- if .Values.session.systemPrompt }}
    - name: SKULD__SESSION__SYSTEM_PROMPT
      value: {{ .Values.session.systemPrompt | quote }}
    {{- end }}
    {{- if .Values.session.initialPrompt }}
    - name: SKULD__SESSION__INITIAL_PROMPT
      value: {{ .Values.session.initialPrompt | quote }}
    {{- end }}
  resources:
    {{- toYaml .Values.resources | nindent 4 }}
  volumeMounts:
    {{- include "skuld.scratchMounts" (dict "root" . "container" (.containerName | default "skuld")) | nindent 4 }}
    - name: {{ .configVolume | default "skuld-config" }}
      mountPath: /etc/skuld/config.yaml
      subPath: {{ .configKey | default "config.yaml" }}
      readOnly: true
    {{- with (.Values.unit | default dict).meshDir }}
    - name: mesh
      mountPath: {{ . }}
    {{- end }}
    {{- if .Values.persistence.enabled }}
    - name: sessions
      mountPath: {{ .Values.persistence.mountPath }}
    {{- end }}
    {{- if .Values.homeVolume.enabled }}
    - name: home
      mountPath: {{ .Values.homeVolume.mountPath }}
    {{- if .Values.homeVolume.credentialFiles.secretName }}
    - name: credential-files
      mountPath: {{ .Values.homeVolume.credentialFiles.secretMountPath }}
      readOnly: true
    {{- end }}
    {{- end }}
    {{- if .Values.secretManifest }}
    - name: secret-manifest
      mountPath: /run/secrets/manifest.json
      subPath: manifest.json
      readOnly: true
    {{- end }}
    {{- range .Values.extraVolumeMounts }}
    - {{- toYaml . | nindent 6 }}
    {{- end }}
{{- end }}
