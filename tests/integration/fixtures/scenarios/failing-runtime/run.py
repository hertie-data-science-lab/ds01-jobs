"""Job that fails with a RuntimeError."""

print("Starting job...")
print("Processing step 1...")
raise RuntimeError("Simulated job failure: model checkpoint corrupt")
