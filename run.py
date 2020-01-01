"""Generate ~50MB of output files."""

import os

for i in range(50):
    with open(f"/output/chunk_{i:03d}.bin", "wb") as f:
        f.write(os.urandom(1024 * 1024))
    print(f"Generated chunk {i + 1}/50")

with open("/output/manifest.txt", "w") as f:
    for i in range(50):
        f.write(f"chunk_{i:03d}.bin 1048576\n")
    f.write(f"total_bytes: {50 * 1024 * 1024}\n")
print("Large output test complete: 50MB generated")
