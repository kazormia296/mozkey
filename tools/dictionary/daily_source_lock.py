#!/usr/bin/env python3
"""Read and validate the immutable daily-dictionary source lock."""

from __future__ import annotations

import hashlib
import json
import pathlib
from collections.abc import Mapping
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LOCK_PATH = REPO_ROOT / "tools" / "dictionary" / "daily_sources.lock.json"
LOCK_SCHEMA = "mozkey.daily_dictionary_source_lock.v1"
RELEASE_PROFILE = "release-approved-only"
LOCAL_PROFILE = "local-evaluation"


class SourceLockError(ValueError):
    """The source lock is malformed or does not match a payload."""


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_hex(value: Any, length: int, label: str) -> str:
    if not isinstance(value, str) or len(value) != length:
        raise SourceLockError(f"{label} must be {length} lowercase hexadecimal chars")
    if any(ch not in "0123456789abcdef" for ch in value):
        raise SourceLockError(f"{label} must be lowercase hexadecimal")
    return value


def load_lock(path: pathlib.Path = LOCK_PATH) -> dict[str, Any]:
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceLockError(f"cannot read source lock {path}: {error}") from error
    validate_lock(lock)
    return lock


def validate_lock(lock: Mapping[str, Any]) -> None:
    if lock.get("schema_version") != LOCK_SCHEMA:
        raise SourceLockError("unexpected source lock schema")
    profiles = lock.get("profiles")
    sources = lock.get("sources")
    if not isinstance(profiles, Mapping) or not isinstance(sources, Mapping):
        raise SourceLockError("source lock requires profiles and sources objects")
    for profile_name in (LOCAL_PROFILE, RELEASE_PROFILE):
        profile = profiles.get(profile_name)
        if not isinstance(profile, Mapping):
            raise SourceLockError(f"missing profile: {profile_name}")
        source_ids = profile.get("source_ids")
        if not isinstance(source_ids, list) or not source_ids:
            raise SourceLockError(f"{profile_name}.source_ids must be non-empty")
        if len(source_ids) != len(set(source_ids)):
            raise SourceLockError(f"{profile_name}.source_ids contains duplicates")
        for source_id in source_ids:
            if source_id not in sources:
                raise SourceLockError(f"unknown source in {profile_name}: {source_id}")

    release_ids = set(profiles[RELEASE_PROFILE]["source_ids"])
    local_ids = set(profiles[LOCAL_PROFILE]["source_ids"])
    if not release_ids < local_ids:
        raise SourceLockError("release-approved-only must be a strict local subset")

    for source_id, source in sources.items():
        if not isinstance(source, Mapping):
            raise SourceLockError(f"source must be an object: {source_id}")
        _require_hex(source.get("revision"), 40, f"{source_id}.revision")
        if "tree" in source:
            _require_hex(source.get("tree"), 40, f"{source_id}.tree")
        if not isinstance(source.get("repository"), str):
            raise SourceLockError(f"missing repository: {source_id}")
        if not isinstance(source.get("license_expression"), str):
            raise SourceLockError(f"missing license expression: {source_id}")
        payloads = source.get("payloads")
        if not isinstance(payloads, Mapping) or not payloads:
            raise SourceLockError(f"missing payloads: {source_id}")
        for payload_id, payload in payloads.items():
            if not isinstance(payload, Mapping) or not isinstance(payload.get("path"), str):
                raise SourceLockError(f"invalid payload: {source_id}.{payload_id}")
            _require_hex(
                payload.get("sha256"), 64, f"{source_id}.{payload_id}.sha256"
            )
            if "url" in payload and source["revision"] not in payload["url"]:
                raise SourceLockError(
                    f"payload URL is not revision-pinned: {source_id}.{payload_id}"
                )
        approved = source.get("release_approved") is True
        local_only = source.get("local_evaluation_only") is True
        if source_id in release_ids and (not approved or local_only):
            raise SourceLockError(f"release profile contains unapproved source: {source_id}")
        if source_id not in release_ids and approved:
            raise SourceLockError(f"approved source missing from release profile: {source_id}")


def source(lock: Mapping[str, Any], source_id: str) -> Mapping[str, Any]:
    sources = lock["sources"]
    if source_id not in sources:
        raise SourceLockError(f"unknown source: {source_id}")
    return sources[source_id]


def verify_payload(
    lock: Mapping[str, Any],
    source_id: str,
    payload_id: str,
    path: pathlib.Path,
) -> None:
    payloads = source(lock, source_id)["payloads"]
    if payload_id not in payloads:
        raise SourceLockError(f"unknown payload: {source_id}.{payload_id}")
    expected = payloads[payload_id]["sha256"]
    actual = sha256_file(path)
    if actual != expected:
        raise SourceLockError(
            f"SHA-256 mismatch for {source_id}.{payload_id}: "
            f"expected {expected}, got {actual}"
        )
