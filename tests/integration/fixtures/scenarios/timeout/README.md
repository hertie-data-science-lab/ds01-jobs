# ds01-test-timeout
Job that runs forever. Submit with a short timeout to test timeout enforcement.
Usage: ds01-submit run <url> --gpus 1 --timeout 60
Expected: killed after timeout, status shows timeout error.
