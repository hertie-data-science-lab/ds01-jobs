# ds01-test-job

Minimal test job for ds01-jobs integration tests.

Runs `nvidia-smi` and writes output to `/output/gpu.txt`, verifying the full GPU pipeline works end-to-end.

Used by `tests/integration/test_lifecycle.py` in [ds01-jobs](https://github.com/hertie-data-science-lab/ds01-jobs).
