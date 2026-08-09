---
name: dataset-manifest
description: Publish and verify immutable dataset manifests so training workers avoid repeated full-content hashing.
---

# Dataset Manifest

Use at dataset publication or before a run consumes a dataset. Publication and runtime
verification are intentionally different operations.

## Contract

- Inputs: trusted dataset root, logical dataset ID/version, split declarations, producer.
- Output: canonical manifest containing relative paths, sizes, per-file SHA-256, split
  digest, total bytes, and top-level manifest SHA-256.
- Call condition: the root resolves below an administrator-configured trusted directory.
- Dependencies: local filesystem or object-store adapter; SHA-256 implementation.

## Procedure

At publish time, sort normalized relative paths, reject symlinks escaping the root,
hash content exactly once, then atomically store the immutable manifest. At run time,
verify the signed/approved manifest digest and selected split; do not make every worker
rehash all content unless an explicit integrity audit requests it.

## Failure and safety

- `E_PATH_ESCAPE`: path or symlink leaves the trusted root; fail closed.
- `E_CONTENT_CHANGED`: size or hash mismatch; quarantine the dataset version.
- `E_SPLIT_CHANGED`: fixed split digest differs from the reviewed plan; block the run.
- This Skill is read-only at runtime and never deletes source data.

## Verification and reuse

Rebuilding an unchanged root must produce byte-identical canonical JSON and digest.
Any byte, path, or split change must change the digest. The package applies to local,
MinIO/OSS, and other immutable artifact backends through adapters.

