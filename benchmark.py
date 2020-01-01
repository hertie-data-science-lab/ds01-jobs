"""GPU compute benchmark - matrix multiplication and basic training loop."""

import json
import time

import torch

results = {"test": "gpu-compute", "cuda_available": torch.cuda.is_available()}

if torch.cuda.is_available():
    dev = torch.device("cuda")
    results["gpu_name"] = torch.cuda.get_device_name(0)
    results["gpu_memory_gb"] = round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1)

    # Matrix multiply benchmark
    print("Running matmul benchmark...")
    a = torch.randn(4096, 4096, device=dev)
    b = torch.randn(4096, 4096, device=dev)
    torch.cuda.synchronize()
    t0 = time.time()
    for _ in range(10):
        c = torch.mm(a, b)
    torch.cuda.synchronize()
    matmul_time = time.time() - t0
    results["matmul_10x_4096_seconds"] = round(matmul_time, 3)
    print(f"  10x matmul(4096x4096): {matmul_time:.3f}s")

    # Simple training loop
    print("Running mini training loop...")
    model = torch.nn.Sequential(
        torch.nn.Linear(512, 256),
        torch.nn.ReLU(),
        torch.nn.Linear(256, 10),
    ).to(dev)
    optimizer = torch.optim.Adam(model.parameters())
    losses = []
    t0 = time.time()
    for step in range(100):
        x = torch.randn(64, 512, device=dev)
        y = torch.randint(0, 10, (64,), device=dev)
        loss = torch.nn.functional.cross_entropy(model(x), y)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        losses.append(loss.item())
        if step % 20 == 0:
            print(f"  Step {step}/100 loss={loss.item():.4f}")
    train_time = time.time() - t0
    results["training_100steps_seconds"] = round(train_time, 3)
    results["final_loss"] = round(losses[-1], 4)
else:
    results["error"] = "No CUDA device available"
    print("WARNING: No CUDA device found")

with open("/output/benchmark.json", "w") as f:
    json.dump(results, f, indent=2)
print(f"\nBenchmark complete: {json.dumps(results, indent=2)}")
