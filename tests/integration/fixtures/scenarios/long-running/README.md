# ds01-test-long-running
Simulated training job running ~2 minutes (12 epochs x 10s sleep).
Tests: status polling, phase transitions over time, log streaming.
Expected: completes in ~2min, produces training_results.json.
