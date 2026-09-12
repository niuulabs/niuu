#!/usr/bin/env bash
# Build PostgreSQL + pgvector from source into INSTALL_PREFIX.
#
# Required environment variables:
#   POSTGRES_VERSION  — e.g. "17.9"
#   PGVECTOR_VERSION  — e.g. "0.8.2"
#   INSTALL_PREFIX    — absolute path for the install (e.g. build/pginstall)
#
# Idempotent: skips the build when the installed postgres binary already
# matches the requested version.
set -euo pipefail

: "${POSTGRES_VERSION:?POSTGRES_VERSION is required}"
: "${PGVECTOR_VERSION:?PGVECTOR_VERSION is required}"
: "${INSTALL_PREFIX:?INSTALL_PREFIX is required}"

mkdir -p "$INSTALL_PREFIX"
INSTALL_PREFIX="$(cd "$INSTALL_PREFIX" && pwd)"
BUILD_DIR="${BUILD_DIR:-build/pg_build}"
NPROC="${NPROC:-$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

# -------------------------------------------------------------------------
# Skip if already built at the correct version
# -------------------------------------------------------------------------
if [ -x "$INSTALL_PREFIX/bin/postgres" ]; then
    installed=$("$INSTALL_PREFIX/bin/postgres" --version | grep -oE '[0-9]+\.[0-9]+' | head -1)
    if [ "$installed" = "$POSTGRES_VERSION" ] &&
       [ -f "$INSTALL_PREFIX/share/postgresql/extension/pg_trgm.control" ] &&
       [ -f "$INSTALL_PREFIX/share/postgresql/extension/pgcrypto.control" ]; then
        echo "PostgreSQL $POSTGRES_VERSION already installed at $INSTALL_PREFIX — skipping build."
        exit 0
    fi
    echo "Installed version ($installed) does not match target ($POSTGRES_VERSION) — rebuilding."
fi

mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

# -------------------------------------------------------------------------
# PostgreSQL
# -------------------------------------------------------------------------
PG_TARBALL="postgresql-${POSTGRES_VERSION}.tar.bz2"
PG_URL="https://ftp.postgresql.org/pub/source/v${POSTGRES_VERSION}/${PG_TARBALL}"
PG_SRC="postgresql-${POSTGRES_VERSION}"

if [ ! -f "$PG_TARBALL" ]; then
    echo "Downloading PostgreSQL ${POSTGRES_VERSION}..."
    curl -fSL -o "$PG_TARBALL" "$PG_URL"
fi

if [ ! -d "$PG_SRC" ]; then
    echo "Extracting PostgreSQL source..."
    tar xjf "$PG_TARBALL"
fi

echo "Configuring PostgreSQL ${POSTGRES_VERSION}..."
cd "$PG_SRC"
./configure \
    --prefix="$INSTALL_PREFIX" \
    --without-readline \
    --without-icu

echo "Building PostgreSQL (${NPROC} jobs)..."
# Unset nested make variables to avoid conflicts with PostgreSQL's own Makefiles
unset MAKELEVEL MAKEFLAGS MFLAGS 2>/dev/null || true
make -j"$NPROC"

echo "Installing PostgreSQL..."
make install

# gbrain requires trigram search and cryptographic functions alongside pgvector.
make -C contrib/pg_trgm -j"$NPROC" install
# Link libcrypto statically so the distributed extension needs no Homebrew paths.
OPENSSL_LIBDIR="$(pkg-config --variable=libdir openssl)"
make -C contrib/pgcrypto -j"$NPROC" \
    PG_CPPFLAGS="$(pkg-config --cflags openssl)" \
    SHLIB_LINK='$(BE_DLLLIBS) '"$OPENSSL_LIBDIR/libcrypto.a -lz $(pkg-config --static --libs-only-other openssl)" \
    install

cd ..

# -------------------------------------------------------------------------
# pgvector
# -------------------------------------------------------------------------
VEC_TARBALL="pgvector-${PGVECTOR_VERSION}.tar.gz"
VEC_URL="https://github.com/pgvector/pgvector/archive/refs/tags/v${PGVECTOR_VERSION}.tar.gz"
VEC_SRC="pgvector-${PGVECTOR_VERSION}"

if [ ! -f "$VEC_TARBALL" ]; then
    echo "Downloading pgvector ${PGVECTOR_VERSION}..."
    curl -fSL -o "$VEC_TARBALL" "$VEC_URL"
fi

if [ ! -d "$VEC_SRC" ]; then
    echo "Extracting pgvector source..."
    mkdir -p "$VEC_SRC"
    tar xzf "$VEC_TARBALL" -C "$VEC_SRC" --strip-components=1
fi

echo "Building pgvector ${PGVECTOR_VERSION}..."
cd "$VEC_SRC"
unset MAKELEVEL MAKEFLAGS MFLAGS 2>/dev/null || true
PG_CONFIG="$INSTALL_PREFIX/bin/pg_config" make -j"$NPROC"
PG_CONFIG="$INSTALL_PREFIX/bin/pg_config" make install
cd ..

# -------------------------------------------------------------------------
# Strip binaries and remove unnecessary files to reduce bundle size
# -------------------------------------------------------------------------
echo "Stripping binaries..."
# Strip all binaries EXCEPT postgres itself — extensions (dylibs) need the
# postgres binary to export symbols like CurrentMemoryContext at dlopen time.
find "$INSTALL_PREFIX/bin" -type f -perm +111 ! -name postgres -exec strip {} + 2>/dev/null || true
strip -x "$INSTALL_PREFIX/bin/postgres" 2>/dev/null || true
find "$INSTALL_PREFIX/lib" -type f \( -name '*.so' -o -name '*.dylib' \) -exec strip -x {} + 2>/dev/null || true

# Remove files not needed at runtime
rm -rf "$INSTALL_PREFIX/include"
rm -rf "$INSTALL_PREFIX/lib/pgxs"
rm -rf "$INSTALL_PREFIX/lib/pkgconfig"
find "$INSTALL_PREFIX/lib" -name '*.a' -delete 2>/dev/null || true

# -------------------------------------------------------------------------
# macOS: fix dylib install names for relocatability
# -------------------------------------------------------------------------
if [ "$(uname -s)" = "Darwin" ]; then
    echo "Fixing macOS dylib install names for relocatability..."

    _rewrite_refs() {
        # Usage: _rewrite_refs <binary> <replacement_prefix>
        # Rewrites references to $INSTALL_PREFIX/lib/* using install_name_tool.
        local target="$1" prefix="$2"
        local refs
        refs="$(otool -L "$target" 2>/dev/null | grep "$INSTALL_PREFIX/lib" | awk '{print $1}' || true)"
        [ -z "$refs" ] && return 0
        while IFS= read -r ref; do
            local base
            base="$(basename "$ref")"
            install_name_tool -change "$ref" "${prefix}${base}" "$target" 2>/dev/null || true
        done <<< "$refs"
    }

    # Fix library install names to use @loader_path
    for dylib in "$INSTALL_PREFIX/lib"/*.dylib; do
        [ -f "$dylib" ] || continue
        install_name_tool -id "@loader_path/$(basename "$dylib")" "$dylib" 2>/dev/null || true
    done

    # Fix references in binaries to point to @executable_path/../lib/
    for bin in "$INSTALL_PREFIX/bin"/*; do
        [ -f "$bin" ] || continue
        _rewrite_refs "$bin" "@executable_path/../lib/"
    done

    # Fix inter-library references
    for dylib in "$INSTALL_PREFIX/lib"/*.dylib; do
        [ -f "$dylib" ] || continue
        _rewrite_refs "$dylib" "@loader_path/"
    done

    # Fix extension modules in lib/postgresql/
    for ext in "$INSTALL_PREFIX/lib/postgresql"/*.dylib; do
        [ -f "$ext" ] || continue
        _rewrite_refs "$ext" "@loader_path/../"
    done
fi

installed_ver=$("$INSTALL_PREFIX/bin/postgres" --version | grep -oE '[0-9]+\.[0-9]+' | head -1)
echo "PostgreSQL ${installed_ver} + pgvector ${PGVECTOR_VERSION} installed at ${INSTALL_PREFIX}"
