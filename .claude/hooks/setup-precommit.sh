#!/bin/bash
# Setup pre-commit hooks and required tooling.
# This runs at the start of each Claude session.

set -e

cd "$CLAUDE_PROJECT_DIR"
export PATH="${HOME}/.local/bin:${PATH}"

# Use the tool shim so installs stay version-pinned without ad hoc pip usage.
_pre_commit() {
    pre-commit "$@"
}

ensure_uv() {
    if command -v uv &> /dev/null; then
        return 0
    fi
    echo "uv is required to install local tooling. Please install uv and re-run this hook setup." >&2
    exit 1
}

verify_sha256() {
    local expected="$1"
    local file="$2"
    if command -v sha256sum &> /dev/null; then
        echo "${expected}  ${file}" | sha256sum -c -
    else
        echo "${expected}  ${file}" | shasum -a 256 -c -
    fi
}

# ── Tool installation ─────────────────────────────────────────────

ensure_uv

# pre-commit framework
if ! command -v pre-commit &> /dev/null; then
    echo "Installing pre-commit..."
    uv tool install pre-commit==4.5.1 --quiet
fi

# ruff (Python linter + formatter) — version pinned for reproducibility
if ! command -v ruff &> /dev/null; then
    echo "Installing ruff..."
    uv tool install ruff==0.11.2 --quiet
fi

# trufflehog (secret scanner) — pinned to specific release, not main branch
if ! command -v trufflehog &> /dev/null; then
    echo "Installing trufflehog..."
    TRUFFLEHOG_VERSION="v3.88.1"
    TRUFFLEHOG_BIN="${HOME}/.local/bin"
    mkdir -p "${TRUFFLEHOG_BIN}"

    case "$(uname -s)-$(uname -m)" in
        Darwin-arm64)
            TRUFFLEHOG_ARCHIVE="trufflehog_3.88.1_darwin_arm64.tar.gz"
            TRUFFLEHOG_SHA256="42293789b7139030e2e4d2ff3ee7e376987174b454d9096c3c3da418cb411cbb"
            ;;
        Darwin-x86_64)
            TRUFFLEHOG_ARCHIVE="trufflehog_3.88.1_darwin_amd64.tar.gz"
            TRUFFLEHOG_SHA256="e7e22867eb6eba0adad0fadd3c2b372971567625fcfaf11ffc325267e84964af"
            ;;
        Linux-aarch64)
            TRUFFLEHOG_ARCHIVE="trufflehog_3.88.1_linux_arm64.tar.gz"
            TRUFFLEHOG_SHA256="c85a0c1ce3a4d2e4f2b6f9cd4a40446e9294214b31f55edd548e66769e10cf32"
            ;;
        Linux-x86_64)
            TRUFFLEHOG_ARCHIVE="trufflehog_3.88.1_linux_amd64.tar.gz"
            TRUFFLEHOG_SHA256="0de286551c75b2f890f2c577ca97d761510641ecf3cabfdcdf4897c2c9901794"
            ;;
        *)
            echo "Unsupported platform for trufflehog install: $(uname -s)-$(uname -m)" >&2
            exit 1
            ;;
    esac

    TMPDIR="$(mktemp -d)"
    trap 'rm -rf "${TMPDIR}"' RETURN
    curl -fsSL "https://github.com/trufflesecurity/trufflehog/releases/download/${TRUFFLEHOG_VERSION}/${TRUFFLEHOG_ARCHIVE}" \
        -o "${TMPDIR}/${TRUFFLEHOG_ARCHIVE}"
    verify_sha256 "${TRUFFLEHOG_SHA256}" "${TMPDIR}/${TRUFFLEHOG_ARCHIVE}"
    tar -xzf "${TMPDIR}/${TRUFFLEHOG_ARCHIVE}" -C "${TMPDIR}"
    install -m 0755 "${TMPDIR}/trufflehog" "${TRUFFLEHOG_BIN}/trufflehog"
fi

# web dependencies (prettier, eslint)
if [ -d "web" ] && [ ! -d "web/node_modules" ]; then
    echo "Installing web dependencies..."
    (cd web && npm ci --ignore-scripts --quiet)
fi

# ── Hook installation ─────────────────────────────────────────────

if [ ! -f ".git/hooks/pre-commit" ] || ! grep -q "pre-commit" ".git/hooks/pre-commit" 2>/dev/null; then
    echo "Installing pre-commit hooks..."
    _pre_commit install --install-hooks
    echo "Pre-commit hooks installed successfully"
else
    echo "Pre-commit hooks already installed"
fi
