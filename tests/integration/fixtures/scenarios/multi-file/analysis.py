"""Simulated data analysis job with multiple output types."""

import json

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

print("Generating synthetic dataset...")
np.random.seed(42)
n = 1000
data = pd.DataFrame(
    {
        "x": np.random.randn(n),
        "y": np.random.randn(n) * 2 + 1,
        "group": np.random.choice(["A", "B", "C"], n),
    }
)
data["z"] = data["x"] * 0.5 + data["y"] * 0.3 + np.random.randn(n) * 0.1

print("Running analysis...")
summary = data.groupby("group").agg(["mean", "std", "count"]).round(4)

# CSV output
data.to_csv("/output/dataset.csv", index=False)
print(f"  Wrote dataset.csv ({len(data)} rows)")

# Plot
fig, axes = plt.subplots(1, 2, figsize=(12, 5))
for grp, grp_data in data.groupby("group"):
    axes[0].scatter(grp_data["x"], grp_data["y"], label=grp, alpha=0.5, s=10)
axes[0].set_xlabel("x")
axes[0].set_ylabel("y")
axes[0].legend()
axes[0].set_title("Scatter by Group")
data["z"].hist(ax=axes[1], bins=30)
axes[1].set_title("Distribution of z")
plt.tight_layout()
plt.savefig("/output/analysis.png", dpi=100)
print("  Wrote analysis.png")

# JSON summary
results = {
    "test": "multi-file",
    "n_rows": len(data),
    "groups": sorted(data["group"].unique().tolist()),
    "z_mean": round(data["z"].mean(), 4),
    "z_std": round(data["z"].std(), 4),
    "output_files": ["dataset.csv", "analysis.png", "summary.json"],
}
with open("/output/summary.json", "w") as f:
    json.dump(results, f, indent=2)
print("  Wrote summary.json")
print("Analysis complete")
