# Sharing and scheduling one GPU across many workloads

Serving multiple models or many concurrent users from limited GPU capacity is a core systems-
integration problem. NVIDIA provides several complementary mechanisms, and they can be combined.

Multi-Instance GPU (MIG) partitions a single physical GPU into as many as seven instances, each fully
isolated with its own high-bandwidth memory, cache, and compute cores, delivering guaranteed quality
of service and hardware fault isolation. MIG is supported from the Ampere generation onward (compute
capability 8.0 and higher), including A100 and A30, Hopper GPUs such as H100 and H200, and Blackwell
platforms such as B200 and GB200, with continued support on the Blackwell and Rubin architectures. MIG
is the right choice when several tenants need strict, predictable isolation on one card.

The Multi-Process Service (MPS) is a lightweight runtime that transparently lets CUDA work from
multiple processes run co-operatively on one GPU, overlapping kernels and memory copies and sharing a
single set of GPU scheduling resources to reduce context-switch overhead. MPS is useful when
individual processes do not saturate the GPU, so several can run concurrently. Time-slicing is a third
option that interleaves workloads when hard isolation is not required.

At the serving layer, the Triton Inference Server runs multiple model instances concurrently on one
GPU and uses dynamic batching to keep utilization high, so several models or replicas coexist behind a
single endpoint.

In Kubernetes, the NVIDIA GPU Operator and the NVIDIA device plugin expose these sharing modes to the
scheduler. Time-slicing is configured by advertising a number of replicas per GPU that pods can
request; unlike MIG it provides no memory or fault isolation between replicas, but time-slicing and MIG
can be combined to share MIG instances. NVIDIA has also open-sourced the KAI Scheduler (Apache 2.0),
a Kubernetes-native scheduler for AI at scale, developed from the Run:ai platform NVIDIA acquired. KAI
adds gang scheduling of pod groups, GPU sharing across pods, fair-share quotas and weights, and
workload consolidation (bin-packing) to reduce GPU fragmentation, and it can run alongside other
schedulers.

A common pattern for smaller or single-GPU deployments is an application-level broker: a service that
serializes access to the GPU and enforces a policy such as one heavy generative model resident at a
time while a lightweight embedding model co-resides. This keeps a 24 GB class GPU from thrashing when
chat, embedding, and image workloads contend, and it degrades gracefully by queuing rather than
over-committing memory. The same instinct scales up to MIG partitions, Triton concurrency, and a
Kubernetes scheduler such as KAI on larger hardware.

For an integrator advising a customer, match the mechanism to the requirement: MIG for strict
multi-tenant isolation, MPS and Triton concurrency with dynamic batching for throughput, GPU Operator
time-slicing for lightweight sharing, KAI Scheduler for cluster-scale orchestration, and a broker
layer to enforce memory-safety policy on constrained hardware.

## Sources (NVIDIA)
- Multi-Instance GPU: https://www.nvidia.com/en-us/technologies/multi-instance-gpu/
- MIG supported GPUs: https://docs.nvidia.com/datacenter/tesla/mig-user-guide/supported-gpus.html
- Multi-Process Service (MPS): https://docs.nvidia.com/deploy/mps/latest/index.html
- GPU Operator time-slicing: https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html
- KAI Scheduler (open source): https://developer.nvidia.com/blog/nvidia-open-sources-runai-scheduler-to-foster-community-collaboration/
- Triton concurrency + batching: https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/tutorials/Conceptual_Guide/Part_2-improving_resource_utilization/README.html
