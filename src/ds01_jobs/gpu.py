"""GPU availability checking via nvidia-smi.

Queries nvidia-smi to determine how many GPUs are currently idle
(less than 100 MiB memory used). Returns 0 gracefully when
nvidia-smi is unavailable (e.g. in CI or on non-GPU machines).
"""

import asyncio

IDLE_MEMORY_THRESHOLD_MIB = 100


async def get_available_gpu_count() -> int:
    """Return the number of idle GPUs based on nvidia-smi memory usage.

    A GPU is considered idle if its memory usage is below
    IDLE_MEMORY_THRESHOLD_MIB. Returns 0 if nvidia-smi is not
    available or exits with a non-zero code.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=index,memory.used",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
    except FileNotFoundError:
        return 0

    if proc.returncode != 0:
        return 0

    idle = 0
    for line in stdout.decode().strip().splitlines():
        parts = line.split(",")
        if len(parts) == 2:
            memory_used = float(parts[1].strip())
            if memory_used < IDLE_MEMORY_THRESHOLD_MIB:
                idle += 1
    return idle


async def get_gpu_count() -> int:
    """Return total GPU count (not just available).

    Used for validation (e.g. rejecting gpu_count > total).
    Returns 0 if nvidia-smi is unavailable.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=index,memory.used",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
    except FileNotFoundError:
        return 0

    if proc.returncode != 0:
        return 0

    lines = [line for line in stdout.decode().strip().splitlines() if line.strip()]
    return len(lines)
