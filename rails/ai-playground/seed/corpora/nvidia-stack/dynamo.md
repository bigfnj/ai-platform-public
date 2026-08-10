# NVIDIA Dynamo — datacenter-scale distributed inference

NVIDIA Dynamo is an open-source, distributed inference-serving framework built to deploy generative AI
models across multi-node environments at data-center scale. It is the orchestration layer above the
inference engines: it does not replace SGLang, TensorRT-LLM, or vLLM, and it builds on the successes
of the Triton Inference Server with a new modular architecture designed for multi-node, distributed
serving of large language and reasoning models. Dynamo is Apache 2.0 licensed, implemented in Rust for
performance with Python for extensibility, and reached a production-ready 1.0 release.

Dynamo's core ideas target the economics and latency of large-scale LLM serving:

Disaggregated serving separates the LLM's context (prefill) and generation (decode) phases onto
distinct GPUs, so each phase can be allocated and optimized independently rather than competing for
the same hardware. This is one of the biggest levers for throughput and cost at scale.

A KV-cache-aware smart router tracks the key-value cache across a large fleet of GPUs and routes each
incoming request to the worker that can reuse the most cache, minimizing costly recomputation. A GPU
Planner dynamically allocates workers across the prefill and decode phases based on live GPU capacity
and service-level objectives, choosing between disaggregated and aggregated serving to resolve
bottlenecks.

Supporting components include NIXL, a low-latency transfer library that moves data rapidly and
asynchronously across tiers of memory and storage in distributed inference, and a KV Block Manager
that can offload the KV cache to cost-efficient storage such as CPU RAM, local SSDs, or network
storage. Dynamo supports the open inference engines SGLang, NVIDIA TensorRT-LLM, and vLLM as backends,
and reported gains include large throughput improvements on Blackwell-class systems and lower
time-to-first-token for agentic workloads.

Dynamo is being integrated into the rest of the stack: NVIDIA NIM microservices will include Dynamo
capabilities, and Dynamo is supported and available with NVIDIA AI Enterprise. For a systems
integrator, the guidance is a progression: a single NIM or Triton for straightforward serving, and
Dynamo when a deployment must scale across many nodes and needs disaggregated prefill/decode plus
cache-aware routing to hit latency and cost targets.

## Sources (NVIDIA)
- Dynamo product page: https://www.nvidia.com/en-us/ai/dynamo/
- Dynamo (open source): https://github.com/ai-dynamo/dynamo
- Introducing Dynamo (technical blog): https://developer.nvidia.com/blog/introducing-nvidia-dynamo-a-low-latency-distributed-inference-framework-for-scaling-reasoning-ai-models/
- Dynamo 1.0 production-ready: https://developer.nvidia.com/blog/nvidia-dynamo-1-production-ready/
