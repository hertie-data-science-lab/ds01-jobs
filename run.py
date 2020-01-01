"""Infinite loop job — should be killed by timeout."""

import time

print("Starting infinite loop (should be killed by timeout)...")
i = 0
while True:
    i += 1
    if i % 30 == 0:
        print(f"Still running... {i}s elapsed")
    time.sleep(1)
