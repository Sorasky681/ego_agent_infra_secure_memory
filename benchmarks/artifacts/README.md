# Committed benchmark evidence

`2026-08-29-local-cpu.json` is canonical raw output from:

```bash
python -m benchmarks.runner \
  --profiles all \
  --repetitions 5 \
  --seed 20260829 \
  --strict \
  --output-json benchmarks/artifacts/2026-08-29-local-cpu.json \
  --output-md benchmarks/artifacts/2026-08-29-local-cpu.md
```

The artifact records 210 trials: 14 scenarios × 5 repetitions × 3 profiles. Its environment
metadata explicitly identifies a local macOS arm64, CPython 3.9.6, non-GPU run and notes that
the worktree contained the benchmark implementation before commit (`git_dirty: true`).

Verify file integrity from this directory with:

```bash
shasum -a 256 -c 2026-08-29-local-cpu.sha256
```

The raw-file checksum and the result's semantic digest serve different purposes. The raw
checksum covers timestamps and measured latency. The semantic digest excludes timing and
diagnostic detail so a repeated run can prove outcome agreement despite normal wall-clock
jitter. It includes the verified trace and evidence roots when a live target executes. A second
local run with the same corpus/seed produced the same semantic digest:
`59a39f466a0506fdc6246cd13860283a314e8881c58ddda7a5f9930c9b561d80`.
