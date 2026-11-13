#!/usr/bin/env python3
"""Build and verify a deterministic Hermes Switchyard release archive.

The archive boundary is deliberately explicit. This script reads only the
allowlisted Git blobs from the caller-supplied reviewed source commit; it does
not walk dirty worktree files, execute package managers, or include
test/evaluation outputs.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

PLUGIN_NAME = "hermes-switchyard"
SOURCE_MANIFEST_NAME = "SOURCE-MANIFEST.json"
CHECKSUMS_NAME = "SHA256SUMS"
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# Keep this list explicit. Source files, tests, evaluation data, CI files,
# local results, and repository metadata are not release payloads.
RELEASE_FILES = (
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "THIRD_PARTY.md",
    "__init__.py",
    "after-install.md",
    "docs/README.md",
    "docs/CONCEPTS.md",
    "docs/CONFIGURATION.md",
    "docs/RECORD-TRIAGE.md",
    "docs/RELEASE.md",
    "docs/SETUP.md",
    "docs/AUTOMATIC-SETUP.md",
    "docs/AUTOMATIC-INTEGRATION.md",
    "docs/ADAPTIVE-REASONING-EFFORT.md",
    "docs/SESSION-SEARCH-RERANK.md",
    "docs/WEB-SCREENING-PROTOTYPE.md",
    "docs/SOURCE-FINDER.md",
    "docs/AWESOME-JEV-EVALUATION.md",
    "docs/VALUE-EVALUATION.md",
    "docs/BENCHMARKS.md",
    "docs/DOM-BROWSER-BACKEND.md",
    "docs/LINT-SKILLS.md",
    "docs/FEATURE-EXPANSION.md",
    "docs/MODEL-ROUTING.md",
    "docs/OUTCOME-LABELS.md",
    "docs/TEST-MATRIX.md",
    "docs/CI.md",
    "docs/FIRST-RUN.md",
    "docs/WOW-LOCAL-REPORT.md",
    "docs/benchmarks/awesome-jev-native-v2.json",
    "docs/benchmarks/live-selector-c6d9b28.json",
    "docs/benchmarks/live-selector-c8e6008.json",
    "docs/benchmarks/live-selector-7dc77c8.json",
    "docs/benchmarks/live-selector-dd4477a.json",
    "docs/benchmarks/multi-skill-dd4477a.json",
    "docs/benchmarks/feature-battery-c8e6008.json",
    "docs/benchmarks/computer-use-goal-verified-felidae.json",
    "docs/assets/hermes-switchyard-branding.png",
    "docs/assets/hermes-switchyard-overview.png",
    "docs/assets/effort-receipts-recorded.png",
    "hermes_switchyard/__init__.py",
    "hermes_switchyard/automatic.py",
    "hermes_switchyard/approval_review.py",
    "hermes_switchyard/catalog_scan.py",
    "hermes_switchyard/output_pruning.py",
    "hermes_switchyard/retrieved_screen.py",
    "hermes_switchyard/web_screening_prototype.py",
    "hermes_switchyard/stuck_detection.py",

    "hermes_switchyard/browser_use.py",
    "hermes_switchyard/browser_plan.py",
    "hermes_switchyard/egress.py",
    "hermes_switchyard/egress_redaction.py",
    "hermes_switchyard/_win_acl.py",
    "hermes_switchyard/receipt_history.py",
    "hermes_switchyard/receipt_output.py",
    "hermes_switchyard/receipt_state.py",
    "hermes_switchyard/runtime_provenance.py",
    "hermes_switchyard/usage_ledger.py",
    "hermes_switchyard/record_triage.py",
    "hermes_switchyard/client.py",
    "hermes_switchyard/combined_decisions.py",
    "hermes_switchyard/source_find.py",
    "hermes_switchyard/source_bundle.py",
    "hermes_switchyard/source_prefetch.py",
    "hermes_switchyard/computer_use.py",
    "hermes_switchyard/defer_tool_schemas.py",
    "hermes_switchyard/destination_policy.py",
    "hermes_switchyard/host_compat.py",
    "hermes_switchyard/legacy_cleanup.py",
    "hermes_switchyard/model_policy.py",
    "hermes_switchyard/model_route_adapter.py",
    "hermes_switchyard/model_registry.py",
    "hermes_switchyard/outcome_labels.py",
    "hermes_switchyard/reasoning_effort_adapter.py",
    "hermes_switchyard/routing.py",
    "hermes_switchyard/schemas.py",
    "hermes_switchyard/session_search_rerank.py",
    "hermes_switchyard/skill_lint.py",
    "hermes_switchyard/skill_duplicates.py",
    "hermes_switchyard/trivial_turn.py",
    "hermes_switchyard/two_stage_routing.py",
    "hermes_switchyard/wow.py",
    "hermes_switchyard/skills/hermes-switchyard-operations/SKILL.md",
    "plugin.yaml",
)
MANIFEST_KEYS = frozenset(
    {"files", "format", "manifest_version", "plugin", "source_sha", "version"}
)
FILE_ENTRY_KEYS = frozenset({"path", "sha256", "size"})
REGULAR_BLOB_MODES = frozenset({"100644", "100755"})
MAX_MEMBER_UNCOMPRESSED_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 64 * 1024 * 1024


class ReleaseError(RuntimeError):
    """Raised when the source tree cannot produce a valid release payload."""


class ReleaseVerificationError(ReleaseError):
    """Raised when an archive fails an extraction or content-integrity check."""


def _scalar(text: str, key: str) -> str:
    match = re.search(rf"(?m)^\s*{re.escape(key)}:\s*([^#\r\n]+)", text)
    if match is None:
        raise ReleaseError(f"plugin.yaml is missing {key}")
    value = match.group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    if not value:
        raise ReleaseError(f"plugin.yaml has an empty {key}")
    return value


def _plugin_metadata_text(text: str) -> dict[str, Any]:
    name = _scalar(text, "name")
    version = _scalar(text, "version")
    manifest_version = _scalar(text, "manifest_version")
    if name != PLUGIN_NAME:
        raise ReleaseError(f"plugin.yaml name is not {PLUGIN_NAME!r}")
    if not VERSION_RE.fullmatch(version):
        raise ReleaseError("plugin.yaml version is not a release version")
    if manifest_version != "1":
        raise ReleaseError("plugin.yaml manifest_version must be 1")
    return {
        "name": name,
        "version": version,
        "manifest_version": int(manifest_version),
    }


def _plugin_metadata(root: Path) -> dict[str, Any]:
    manifest = root / "plugin.yaml"
    if not manifest.is_file():
        raise ReleaseError("required release file missing: plugin.yaml")
    return _plugin_metadata_text(manifest.read_text(encoding="utf-8"))


def _plugin_metadata_bytes(data: bytes) -> dict[str, Any]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseError("committed plugin.yaml is not UTF-8") from exc
    return _plugin_metadata_text(text)


def _git_output(root: Path, *args: str, operation: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ReleaseError("git is required for source-bound release archives") from exc
    except subprocess.CalledProcessError as exc:
        raise ReleaseError(f"git source lookup failed while trying to {operation}") from exc
    return result.stdout


def _source_files_from_git(root: Path, source_sha: str) -> dict[str, bytes]:
    """Read the allowlisted payload from immutable blobs in one Git commit."""
    root = Path(root).resolve()
    source_sha = _require_source_sha(source_sha)
    object_type = _git_output(
        root, "cat-file", "-t", source_sha, operation="verify the source commit"
    ).strip()
    if object_type != b"commit":
        raise ReleaseError("source SHA must identify a commit object directly")
    listing = _git_output(
        root,
        "ls-tree",
        "-z",
        "-r",
        "--full-tree",
        source_sha,
        "--",
        *sorted(RELEASE_FILES),
        operation="list the source tree",
    )
    expected = set(RELEASE_FILES)
    entries: dict[str, bytes] = {}
    for record in listing.split(b"\0"):
        if not record:
            continue
        try:
            header, path_bytes = record.split(b"\t", 1)
            mode, entry_type, object_id = header.decode("ascii").split()
            path = path_bytes.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ReleaseError("git source tree contains an unreadable entry") from exc
        if path not in expected:
            continue
        if path in entries:
            raise ReleaseError(f"git source tree contains duplicate entry: {path}")
        if entry_type != "blob" or mode not in REGULAR_BLOB_MODES:
            raise ReleaseError(f"release entry is not an ordinary blob: {path}")
        if not re.fullmatch(r"[0-9a-f]{40}", object_id):
            raise ReleaseError(f"git source tree has an invalid blob ID: {path}")
        entries[path] = _git_output(
            root,
            "cat-file",
            "blob",
            object_id,
            operation=f"read the source blob {path}",
        )
    missing = expected - entries.keys()
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ReleaseError(f"git source commit is missing release files: {missing_list}")
    return {path: entries[path] for path in sorted(RELEASE_FILES)}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.create_version = 20
    info.extract_version = 20
    info.external_attr = 0o100644 << 16
    info.extra = b""
    info.comment = b""
    return info


def _write_member(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    archive.writestr(_zip_info(name), data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _require_source_sha(source_sha: str) -> str:
    if not isinstance(source_sha, str) or not SHA_RE.fullmatch(source_sha):
        raise ReleaseError("source SHA must be an exact 40-character lowercase hexadecimal commit")
    return source_sha


def _manifest_for(
    metadata: dict[str, Any], source_sha: str, contents: dict[str, bytes]
) -> dict[str, Any]:
    return {
        "format": 1,
        "manifest_version": metadata["manifest_version"],
        "plugin": metadata["name"],
        "source_sha": source_sha,
        "version": metadata["version"],
        "files": [
            {"path": path, "sha256": _sha256(contents[path]), "size": len(contents[path])}
            for path in sorted(contents)
        ],
    }


def _checksums_for(contents: dict[str, bytes], source_manifest: bytes) -> bytes:
    rows = [f"{_sha256(contents[path])}  {path}" for path in sorted(contents)]
    rows.append(f"{_sha256(source_manifest)}  {SOURCE_MANIFEST_NAME}")
    return ("\n".join(rows) + "\n").encode("utf-8")


def build_release(root: Path, output_dir: Path, source_sha: str, version: str | None = None) -> Path:
    """Build, extract-check, and return one deterministic ZIP archive."""
    root = Path(root).resolve()
    output_dir = Path(output_dir).resolve()
    source_sha = _require_source_sha(source_sha)
    contents = _source_files_from_git(root, source_sha)
    metadata = _plugin_metadata_bytes(contents["plugin.yaml"])
    if version is not None and version != metadata["version"]:
        raise ReleaseError("requested version does not match plugin.yaml")

    source_manifest = _json_bytes(_manifest_for(metadata, source_sha, contents))
    checksums = _checksums_for(contents, source_manifest)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f"hermes-switchyard-{metadata['version']}.zip"
    if archive_path.exists():
        archive_path.unlink()

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(contents):
            _write_member(archive, path, contents[path])
        _write_member(archive, SOURCE_MANIFEST_NAME, source_manifest)
        _write_member(archive, CHECKSUMS_NAME, checksums)

    verify_archive(
        archive_path,
        source_root=root,
        expected_source_sha=source_sha,
        expected_version=metadata["version"],
    )
    return archive_path


def _safe_member_name(name: str) -> None:
    pure = PurePosixPath(name)
    if (
        not name
        or pure.is_absolute()
        or name != pure.as_posix()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or name.endswith("/")
    ):
        raise ReleaseVerificationError("archive contains an unsafe member name")


def _manifest_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if set(manifest) != MANIFEST_KEYS:
        raise ReleaseVerificationError("source manifest has an unexpected key")
    if type(manifest["format"]) is not int or manifest["format"] != 1:
        raise ReleaseVerificationError("source manifest has an unsupported format")
    if manifest["plugin"] != PLUGIN_NAME:
        raise ReleaseVerificationError("source manifest has an unsupported plugin")
    if type(manifest["manifest_version"]) is not int or manifest["manifest_version"] != 1:
        raise ReleaseVerificationError("source manifest has an unsupported manifest version")
    source_sha = manifest["source_sha"]
    version = manifest["version"]
    if not isinstance(source_sha, str) or not SHA_RE.fullmatch(source_sha):
        raise ReleaseVerificationError("source manifest does not contain an exact source SHA")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise ReleaseVerificationError("source manifest does not contain a release version")
    files = manifest["files"]
    if not isinstance(files, list) or not files:
        raise ReleaseVerificationError("source manifest has no file entries")
    seen: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise ReleaseVerificationError("source manifest has a non-object file entry")
        if set(entry) != FILE_ENTRY_KEYS:
            raise ReleaseVerificationError("source manifest has an unexpected file-entry key")
        path = entry["path"]
        digest = entry["sha256"]
        size = entry["size"]
        if (
            not isinstance(path, str)
            or path in seen
            or path not in RELEASE_FILES
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or type(size) is not int
            or size < 0
        ):
            raise ReleaseVerificationError("source manifest contains an invalid file entry")
        _safe_member_name(path)
        seen.add(path)
    if seen != set(RELEASE_FILES):
        raise ReleaseVerificationError("source manifest does not match the release allowlist")
    return files


def _parse_checksums(data: bytes) -> dict[str, str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseVerificationError("SHA256SUMS is not UTF-8") from exc
    result: dict[str, str] = {}
    for line in text.splitlines():
        if not line:
            continue
        digest, separator, path = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", digest) or path in result:
            raise ReleaseVerificationError("SHA256SUMS has an invalid row")
        _safe_member_name(path)
        result[path] = digest
    return result


def _extract_payload(payload: dict[str, bytes], destination: Path) -> None:
    for name in sorted(payload):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload[name])


def _verify_extracted_tree(destination: Path, manifest: dict[str, Any]) -> None:
    root_entrypoint = destination / "__init__.py"
    manifest_path = destination / "plugin.yaml"
    try:
        source = root_entrypoint.read_text(encoding="utf-8")
        ast.parse(source, filename=str(root_entrypoint))
        for path in destination.rglob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        raise ReleaseVerificationError("extracted Python entrypoint or package does not parse") from exc
    try:
        tree = ast.parse(source, filename=str(root_entrypoint))
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise ReleaseVerificationError("extracted Python entrypoint does not parse") from exc
    if not _has_register_binding(tree):
        raise ReleaseVerificationError("extracted entrypoint does not expose register")
    try:
        extracted_metadata = _plugin_metadata(destination)
    except ReleaseError as exc:
        raise ReleaseVerificationError("extracted plugin manifest is invalid") from exc
    if extracted_metadata != {
        "name": manifest["plugin"],
        "version": manifest["version"],
        "manifest_version": manifest["manifest_version"],
    }:
        raise ReleaseVerificationError("extracted manifest does not match the source manifest")
    if not manifest_path.is_file():
        raise ReleaseVerificationError("extracted plugin manifest is missing")
    _verify_packaged_references(destination)


def _has_register_binding(tree: ast.AST) -> bool:
    """Return whether module scope binds the loader-visible ``register`` name."""
    for node in getattr(tree, "body", ()):
        if isinstance(node, ast.FunctionDef) and node.name == "register":
            return True
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound_name = alias.asname or alias.name.split(".", 1)[0]
                if bound_name == "register":
                    return True
    return False


def _bounded_archive_infos(infos: list[zipfile.ZipInfo]) -> None:
    """Reject oversized members before any archive member is decompressed."""
    total = 0
    for info in infos:
        size = info.file_size
        if type(size) is not int or size < 0 or size > MAX_MEMBER_UNCOMPRESSED_BYTES:
            raise ReleaseVerificationError("archive member exceeds the uncompressed-size limit")
        total += size
        if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ReleaseVerificationError("archive exceeds the cumulative uncompressed-size limit")


def _verify_packaged_references(destination: Path) -> None:
    """Ensure relative links in the checked packaged guides resolve in the archive."""
    root = destination.resolve()
    for document_name in ("README.md", "CHANGELOG.md", "docs/OUTCOME-LABELS.md"):
        document = destination / document_name
        try:
            text = document.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ReleaseVerificationError(f"extracted {document_name} is unreadable") from exc
        references = re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text)
        for raw_reference in references:
            target = raw_reference.strip().split(maxsplit=1)[0].strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or target.startswith("#"):
                continue
            relative = unquote(parsed.path)
            if not relative:
                continue
            candidate = (document.parent / PurePosixPath(relative)).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise ReleaseVerificationError(f"{document_name} contains a link outside the archive") from exc
            if not candidate.is_file():
                raise ReleaseVerificationError(f"{document_name} link target is not packaged: {relative}")


def verify_archive(
    archive_path: Path,
    *,
    source_root: Path | None = None,
    expected_source_sha: str | None = None,
    expected_version: str | None = None,
) -> dict[str, Any]:
    """Verify archive integrity and, optionally, its exact Git source blobs."""
    archive_path = Path(archive_path)
    if source_root is not None and expected_source_sha is None:
        raise ReleaseVerificationError(
            "an expected source SHA is required when verifying against a source root"
        )
    try:
        expected_sha = (
            _require_source_sha(expected_source_sha) if expected_source_sha is not None else None
        )
    except ReleaseError as exc:
        raise ReleaseVerificationError("expected source SHA is not exact lowercase hexadecimal") from exc
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            for name in names:
                _safe_member_name(name)
            if len(names) != len(set(names)):
                raise ReleaseVerificationError("archive contains duplicate members")
            _bounded_archive_infos(infos)
            required = {SOURCE_MANIFEST_NAME, CHECKSUMS_NAME}
            if not required.issubset(names):
                raise ReleaseVerificationError("archive is missing release metadata")
            payload = {name: archive.read(name) for name in names}
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseVerificationError("archive is not a readable ZIP") from exc

    try:
        manifest = json.loads(payload[SOURCE_MANIFEST_NAME].decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseVerificationError("source manifest is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict):
        raise ReleaseVerificationError("source manifest is not an object")
    files = _manifest_entries(manifest)
    expected_members = set(RELEASE_FILES) | {SOURCE_MANIFEST_NAME, CHECKSUMS_NAME}
    if set(names) != expected_members:
        raise ReleaseVerificationError("archive contains files outside the release boundary")
    if expected_sha is not None and manifest["source_sha"] != expected_sha:
        raise ReleaseVerificationError("archive source SHA does not match the requested SHA")
    if expected_version is not None and manifest["version"] != expected_version:
        raise ReleaseVerificationError("archive version does not match the requested version")

    for entry in files:
        path = entry["path"]
        data = payload[path]
        if len(data) != entry["size"] or _sha256(data) != entry["sha256"]:
            raise ReleaseVerificationError(f"source hash mismatch for {path}")

    checksums = _parse_checksums(payload[CHECKSUMS_NAME])
    expected_checksums = {
        **{path: _sha256(payload[path]) for path in RELEASE_FILES},
        SOURCE_MANIFEST_NAME: _sha256(payload[SOURCE_MANIFEST_NAME]),
    }
    if checksums != expected_checksums:
        raise ReleaseVerificationError("SHA256SUMS does not match archive contents")

    with tempfile.TemporaryDirectory(prefix="hermes-switchyard-release-") as scratch:
        destination = Path(scratch)
        _extract_payload(payload, destination)
        _verify_extracted_tree(destination, manifest)
        for path, expected in expected_checksums.items():
            if _sha256((destination / path).read_bytes()) != expected:
                raise ReleaseVerificationError(f"extracted hash mismatch for {path}")

    source_verified = False
    if source_root is not None:
        if expected_sha is None:
            raise ReleaseVerificationError("external source verification has no expected SHA")
        try:
            source_contents = _source_files_from_git(source_root, expected_sha)
        except ReleaseError as exc:
            raise ReleaseVerificationError("external source reference is invalid") from exc
        for path in RELEASE_FILES:
            if payload[path] != source_contents[path]:
                raise ReleaseVerificationError(f"archive does not match source blob for {path}")
        source_verified = True

    return {
        "plugin": manifest["plugin"],
        "version": manifest["version"],
        "source_sha": manifest["source_sha"],
        "members": len(names),
        "integrity_verified": True,
        "source_verified": source_verified,
        "verification": "source-verified" if source_verified else "integrity-only",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path, default=None, help="verify an existing release archive")
    parser.add_argument("--source-sha", default=os.environ.get("GITHUB_SHA"), help="exact 40-hex reviewed source commit")
    parser.add_argument("--source-root", type=Path, default=None, help="Git checkout used for external source verification")
    parser.add_argument("--expected-source-sha", default=None, help="exact source SHA required by archive verification")
    parser.add_argument("--version", default=None, help="require this version to match plugin.yaml")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="source repository root")
    parser.add_argument("--output-dir", type=Path, default=Path("dist"), help="directory for the verified archive")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.verify is not None:
        try:
            result = verify_archive(
                args.verify,
                source_root=args.source_root,
                expected_source_sha=args.expected_source_sha,
                expected_version=args.version,
            )
        except ReleaseError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"verified {args.verify.name} ({result['verification']})")
        return 0
    if args.source_root is not None or args.expected_source_sha is not None:
        print("ERROR: --source-root and --expected-source-sha require --verify", file=sys.stderr)
        return 2
    if not args.source_sha:
        print("ERROR: --source-sha or GITHUB_SHA is required", file=sys.stderr)
        return 2
    try:
        archive = build_release(args.root, args.output_dir, args.source_sha, args.version)
    except ReleaseError as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        return 1
    print(f"built and verified {archive.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

