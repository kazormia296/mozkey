#!/usr/bin/env bash
# Copyright 2026 The Mozkey Authors

set -euo pipefail

readonly SNAPSHOT_DATE="2026/06/01"
cat > /etc/pacman.d/mirrorlist <<EOF
Server = https://archive.archlinux.org/repos/${SNAPSHOT_DATE}/\$repo/os/\$arch
EOF

pacman -Syy --noconfirm
