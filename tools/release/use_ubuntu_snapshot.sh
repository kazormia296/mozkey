#!/usr/bin/env bash
# Copyright 2026 The Mozkey Authors

set -euo pipefail

readonly SNAPSHOT_URL="https://snapshot.ubuntu.com/ubuntu/20260701T000000Z/"
readonly SOURCES_FILE="/etc/apt/sources.list.d/mozkey-snapshot.sources"
readonly TEMP_SOURCES="$(mktemp)"
trap 'rm -f -- "${TEMP_SOURCES}"' EXIT

cat > "${TEMP_SOURCES}" <<EOF
Types: deb
URIs: ${SNAPSHOT_URL}
Suites: noble noble-updates noble-security
Components: main universe restricted multiverse
Architectures: amd64
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF

sudo rm -f -- /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources
sudo install -m 644 "${TEMP_SOURCES}" "${SOURCES_FILE}"
sudo apt-get \
  -o Acquire::Check-Valid-Until=false \
  -o Acquire::Retries=3 \
  update
