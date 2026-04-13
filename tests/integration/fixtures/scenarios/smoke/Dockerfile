FROM nvcr.io/nvidia/cuda:12.6.3-base-ubuntu24.04

RUN mkdir -p /output

CMD nvidia-smi > /output/gpu.txt && echo "ok" > /output/status.txt
