# Build from the repo root so this can COPY the sibling sim_kernel/ package:
#   docker build -f gpu_worker/Dockerfile -t advisoros-gpu-worker .
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY gpu_worker/requirements.txt ./requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

# sim_kernel is the shared math package — same code the backend runs locally when RunPod
# isn't configured. SIM_KERNEL_GPU=true makes backend.py actually import cupy here.
COPY sim_kernel/sim_kernel ./sim_kernel
COPY gpu_worker/handler.py ./handler.py

ENV SIM_KERNEL_GPU=true
CMD ["python3", "-u", "handler.py"]
