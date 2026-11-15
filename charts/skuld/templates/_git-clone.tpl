{{- /*
The init container that clones the repository into the workspace, rendered from the values of the
release itself or, for a unit whose root names no repository, of the member that does.
*/ -}}
{{- define "skuld.gitCloneInit" -}}
- name: git-clone
  image: "{{ .Values.git.image.repository }}:{{ .Values.git.image.tag }}"
  imagePullPolicy: {{ .Values.git.image.pullPolicy }}
  securityContext:
    runAsUser: {{ .Values.securityContext.runAsUser }}
    allowPrivilegeEscalation: false
  command: ["/bin/sh", "-c"]
  args:
    - |
      set -e
      export GIT_TERMINAL_PROMPT=0
      WORKSPACE="{{ include "skuld.workspacePath" . }}"
      REPO_URL={{ .Values.git.repoUrl | replace "'" "'\\''" | squote }}
      BRANCH={{ .Values.git.branch | default "main" | replace "'" "'\\''" | squote }}
      BASE_BRANCH={{ .Values.git.baseBranch | default "" | replace "'" "'\\''" | squote }}
      if ! git -C "$WORKSPACE" rev-parse --git-dir >/dev/null 2>&1; then
        mkdir -p "$WORKSPACE"
        git init "$WORKSPACE"
      fi
      if git -C "$WORKSPACE" remote get-url origin >/dev/null 2>&1; then
        git -C "$WORKSPACE" remote set-url origin "$REPO_URL"
      else
        git -C "$WORKSPACE" remote add origin "$REPO_URL"
      fi
      {{- if .Values.git.credentials.tokenFile }}
      # Persist references, never tokens. Every Git process reads the mounted secret.
      test -s {{ .Values.git.credentials.tokenFile | replace "'" "'\\''" | squote }} || {
        echo "Selected Git integration token was not injected" >&2; exit 1;
      }
      git -C "$WORKSPACE" config niuu.gitTokenFile {{ .Values.git.credentials.tokenFile | replace "'" "'\\''" | squote }}
      git -C "$WORKSPACE" config niuu.gitUsername {{ .Values.git.credentials.username | replace "'" "'\\''" | squote }}
      git -C "$WORKSPACE" config credential.helper ''
      git -C "$WORKSPACE" config credential.useHttpPath true
      git -C "$WORKSPACE" config "credential.$REPO_URL.helper" \
        '!f() { [ "$1" = get ] || return 0; token=$(cat "$(git config niuu.gitTokenFile)") || return 1; [ -n "$token" ] || return 1; printf "username=%s\npassword=%s\n" "$(git config niuu.gitUsername)" "$token"; }; f'
      {{- else if .Values.git.credentials.secretName }}
      git -C "$WORKSPACE" config credential.helper \
        '!f() { [ "$1" = get ] || return 0; printf "username=%s\npassword=%s\n" "${GIT_USERNAME:-x-access-token}" "$GIT_TOKEN"; }; f'
      {{- end }}
      {{- if .Values.git.userName }}
      if ! git -C "$WORKSPACE" config user.name >/dev/null; then
        git -C "$WORKSPACE" config user.name {{ .Values.git.userName | replace "'" "'\\''" | squote }}
      fi
      {{- end }}
      {{- if .Values.git.userEmail }}
      if ! git -C "$WORKSPACE" config user.email >/dev/null; then
        git -C "$WORKSPACE" config user.email {{ .Values.git.userEmail | replace "'" "'\\''" | squote }}
      fi
      {{- end }}
      if git -C "$WORKSPACE" rev-parse HEAD >/dev/null 2>&1; then
        echo "Workspace already contains a valid git repository, skipping clone"
        if [ -n "$BASE_BRANCH" ] && ! git -C "$WORKSPACE" config --get niuu.baseBranch >/dev/null; then
          git -C "$WORKSPACE" config niuu.baseBranch "$BASE_BRANCH"
        fi
      else
        git -C "$WORKSPACE" fetch origin
        if git -C "$WORKSPACE" rev-parse --verify "origin/$BRANCH" >/dev/null 2>&1; then
          git -C "$WORKSPACE" checkout -B "$BRANCH" "origin/$BRANCH"
          echo "Checked out existing branch $BRANCH"
        else
          if [ -n "$BASE_BRANCH" ] && git -C "$WORKSPACE" rev-parse --verify "origin/$BASE_BRANCH" >/dev/null 2>&1; then
            FALLBACK="$BASE_BRANCH"
          else
            FALLBACK=$(git -C "$WORKSPACE" remote show origin | sed -n 's/.*HEAD branch: //p')
            FALLBACK=${FALLBACK:-main}
          fi
          echo "Branch $BRANCH not found on remote, creating from $FALLBACK"
          git -C "$WORKSPACE" checkout -B "$FALLBACK" "origin/$FALLBACK"
          git -C "$WORKSPACE" checkout -b "$BRANCH"
        fi
        # Record where this branch started, so the session's changes can be shown against it.
        RECORDED_BASE="${FALLBACK:-}"
        if [ -z "$RECORDED_BASE" ]; then
          if [ -n "$BASE_BRANCH" ] && \
            git -C "$WORKSPACE" rev-parse --verify "origin/$BASE_BRANCH" >/dev/null 2>&1; then
            RECORDED_BASE="$BASE_BRANCH"
          else
            RECORDED_BASE=$(git -C "$WORKSPACE" remote show origin | sed -n 's/.*HEAD branch: //p')
          fi
        fi
        if [ -n "$RECORDED_BASE" ]; then
          git -C "$WORKSPACE" config niuu.baseBranch "$RECORDED_BASE"
        fi
        echo "Repository cloned successfully"
      fi
  {{- if or .Values.homeVolume.enabled (and .Values.git.credentials.secretName (not .Values.git.credentials.tokenFile)) }}
  env:
    {{- if .Values.homeVolume.enabled }}
    - name: HOME
      value: {{ .Values.homeVolume.mountPath | quote }}
    {{- end }}
    {{- if and .Values.git.credentials.secretName (not .Values.git.credentials.tokenFile) }}
    - name: GIT_USERNAME
      value: {{ .Values.git.credentials.username | default "x-access-token" | quote }}
    - name: GIT_TOKEN
      valueFrom:
        secretKeyRef:
          name: {{ .Values.git.credentials.secretName }}
          key: {{ .Values.git.credentials.tokenKey | default "token" }}
    {{- end }}
  {{- end }}
  volumeMounts:
    - name: sessions
      mountPath: {{ .Values.persistence.mountPath }}
    {{- if .Values.homeVolume.enabled }}
    - name: home
      mountPath: {{ .Values.homeVolume.mountPath }}
      readOnly: true
    {{- end }}
    {{- range .Values.extraVolumeMounts }}
    - {{- toYaml . | nindent 6 }}
    {{- end }}
{{- end }}
