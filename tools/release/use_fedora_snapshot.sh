#!/usr/bin/env bash
# Copyright 2026 The Mozkey Authors

set -euo pipefail

readonly FEDORA_RELEASE="42"
readonly REPOSITORY_FILE="/etc/yum.repos.d/mozkey-fedora-snapshot.repo"

rm -f -- /etc/yum.repos.d/*.repo
cat > "${REPOSITORY_FILE}" <<EOF
[fedora]
name=Fedora ${FEDORA_RELEASE} Everything archive
baseurl=https://archives.fedoraproject.org/pub/archive/fedora/linux/releases/${FEDORA_RELEASE}/Everything/\$basearch/os/
enabled=1
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-${FEDORA_RELEASE}-primary
metadata_expire=never

[updates]
name=Fedora ${FEDORA_RELEASE} updates archive
baseurl=https://archives.fedoraproject.org/pub/archive/fedora/linux/updates/${FEDORA_RELEASE}/Everything/\$basearch/
enabled=1
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-${FEDORA_RELEASE}-primary
metadata_expire=never
EOF

dnf clean all
