# Triton Inference Server and TensorRT-LLM

The Triton Inference Server is NVIDIA's open-source software for serving models in production. It is
multi-framework: backends include TensorRT, PyTorch, ONNX Runtime, OpenVINO, Python, RAPIDS FIL, and
dedicated TensorRT-LLM and vLLM backends, so one serving tier can host many model types behind one
interface. It speaks HTTP/REST and gRPC based on the community KServe protocol.

Triton's throughput features matter for real-time, low-latency workloads. Dynamic batching combines
individual inference requests, server-side, into dynamically formed batches to raise throughput under
a latency budget. Concurrent model execution spins up multiple instances of a model that process
queries in parallel on the same or different GPUs, keeping the hardware busy. Triton also supports
multi-stage pipelines through model ensembles or Business Logic Scripting, and exposes metrics for GPU
utilization, throughput, and latency that teams use to right-size deployments.

TensorRT-LLM is an open-source library (version 1.0) that compiles and optimizes large language models
for NVIDIA GPUs, delivering large real-time inference speedups. Its optimizations include custom
attention kernels, in-flight (continuous) batching, paged key-value caching, quantization (FP8, NVFP4,
INT4 AWQ, INT8 SmoothQuant), and speculative decoding techniques such as EAGLE-3 and multi-token
prediction, along with disaggregated serving and wide expert parallelism. It offers a PyTorch-native
authoring path and a stable production API, and integrates with the broader inference ecosystem,
including NVIDIA Dynamo.

Triton and TensorRT-LLM work together: the TensorRT-LLM backend lets you serve TensorRT-LLM engines
with Triton (supporting in-flight batching, paged attention, tensor/pipeline/expert parallelism, and
multi-node), and that backend's source now lives inside the TensorRT-LLM repository. For very large,
multi-node, data-center-scale deployments, NVIDIA Dynamo is a distributed inference-serving layer that
builds on the successes of Triton with a modular architecture; Dynamo orchestrates above the engines
rather than replacing them, and can drive TensorRT-LLM, vLLM, or SGLang.

A practical adoption path for a systems integrator: prototype against an OpenAI-compatible endpoint;
for single-node or multi-instance production, serve with Triton (using the TensorRT-LLM backend for
optimized LLM inference); and when a deployment must scale across many nodes with disaggregated
prefill and decode, add NVIDIA Dynamo on top. A prebuilt NIM is the fastest route when a supported
model fits the need.

## Sources (NVIDIA)
- Triton Inference Server docs: https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/index.html
- Triton resource utilization (batching, concurrency): https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/tutorials/Conceptual_Guide/Part_2-improving_resource_utilization/README.html
- TensorRT-LLM: https://developer.nvidia.com/tensorrt-llm
- Triton TensorRT-LLM backend: https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/tensorrtllm_backend/README.html
- NVIDIA Dynamo: https://www.nvidia.com/en-us/ai/dynamo/
