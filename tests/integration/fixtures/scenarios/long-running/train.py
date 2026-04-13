"""Simulated long-running training job. Runs for ~2 minutes."""

import json
import time

results = {"epochs": [], "test": "long-running"}
for epoch in range(12):
    time.sleep(10)
    loss = 1.0 / (epoch + 1)
    results["epochs"].append(
        {"epoch": epoch + 1, "loss": round(loss, 4), "elapsed": (epoch + 1) * 10}
    )
    print(f"Epoch {epoch + 1}/12 - loss: {loss:.4f}")

results["final_loss"] = results["epochs"][-1]["loss"]
results["total_seconds"] = 120

with open("/output/training_results.json", "w") as f:
    json.dump(results, f, indent=2)

print("Training complete")
