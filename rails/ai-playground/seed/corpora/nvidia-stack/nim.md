# NVIDIA NIM — Inference Microservices

NVIDIA NIM provides prebuilt, optimized inference microservices for rapidly deploying the latest AI
models on any NVIDIA-accelerated infrastructure: cloud, data center, workstation, and edge. Each NIM
packages a model, an optimized runtime, and a serving layer into a container that keeps data inside
your environment and deploys with a single command, and it scales on Kubernetes. NIM is part of
NVIDIA AI Enterprise, which adds a secure software supply chain, STIG-hardened containers, ongoing
security updates, API stability, and enterprise support for production.

NIM exposes standard, OpenAI-compatible REST APIs. For large language models the endpoints include
`POST /v1/chat/completions` (multi-turn chat with streaming and tool calling), `POST /v1/completions`,
`POST /v1/embeddings` (when serving an embedding model), and `GET /v1/models`. NeMo Retriever
embedding NIMs expose the OpenAI-compatible embeddings API, and reranking NIMs expose a
`POST /v1/ranking` endpoint. Because the API surface is the OpenAI standard, application code that
already targets an OpenAI-style endpoint can point at a NIM by changing only the base URL, with no
rewrite of the client or the RAG orchestration.

The optimized engine inside a NIM depends on the model. The current NIM for large language models
(version 2.0) is built on vLLM as its core inference engine, having consolidated from an earlier
multi-backend container. Across the broader NIM platform, models are served on optimized runtimes
that include NVIDIA TensorRT-LLM, vLLM, and SGLang; the NeMo Retriever embedding and reranking NIMs
run GPU-accelerated on CUDA, TensorRT, and the Triton Inference Server. NIM microservices span many
categories beyond text LLMs, including embedding and reranking, vision-language models, speech
(Riva ASR, translation, and TTS), biology and medical imaging models, and safety guardrails.

There are two ways to consume NIM. Developers can prototype for free against hosted API endpoints in
the NVIDIA API Catalog on build.nvidia.com, powered by DGX Cloud, and then download the same NIM as a
container for self-hosted deployment across clouds, data centers, and RTX workstations. Self-hosted
deployment options include Kubernetes via Helm, KServe, OpenShift, Run:ai, and the NIM Operator, as
well as air-gapped, multi-node, and vGPU environments.

For a Global Systems Integrator, the value is a consistent, self-hostable API contract across many
models and hardware targets: prototype against the hosted OpenAI-compatible endpoint, then promote to
a self-hosted NIM behind the same interface, swapping models or scaling horizontally without changing
application code.

## Sources (NVIDIA)
- NIM product page: https://www.nvidia.com/en-us/ai-data-science/products/nim-microservices/
- NIM for developers: https://developer.nvidia.com/nim
- NIM docs home: https://docs.nvidia.com/nim/index.html
- NIM for LLMs overview (vLLM engine): https://docs.nvidia.com/nim/large-language-models/latest/about-nim-llm/overview.html
- NIM LLM API reference (OpenAI-compatible endpoints): https://docs.nvidia.com/nim/large-language-models/2.0.0/reference/api-reference.html
- Reranking NIM (/v1/ranking): https://docs.nvidia.com/nim/nemo-retriever/text-reranking/latest/overview.html
