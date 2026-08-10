# NVIDIA AI Enterprise and AI Blueprints

NVIDIA AI Enterprise is a fully supported, production-ready commercial software suite of
microservices, frameworks, and libraries for AI development, with GPU orchestration and infrastructure
management. It bundles the pieces an enterprise needs to run AI in production: ready-to-deploy NVIDIA
NIM microservices; NVIDIA NeMo for model training, evaluation, guardrailing, and RAG building blocks;
NVIDIA Omniverse libraries and microservices for physical AI; NVIDIA Run:ai for GPU orchestration
across the AI lifecycle; and NVIDIA Blueprints as reference workflows.

The reason a Global Systems Integrator's regulated clients care about NVIDIA AI Enterprise is the
production hardening around the open models and tools: a secure software supply chain, STIG-hardened
containers, vulnerability mitigation, extended-lifetime production branches, and enterprise support.
For NIM specifically, production branches receive security and bug fixes on a defined lifecycle with
regular security patches, following a documented vulnerability-disclosure process. This is what turns
"we ran a promising prototype" into "our client signed off on it for production."

NVIDIA Blueprints are customizable reference workflows for building agentic AI pipelines at enterprise
scale. Each blueprint packages partner microservices, one or more AI agents, reference code,
customization documentation, sample data, and a Helm chart for deployment, built with NVIDIA Nemotron
models and NIM and NeMo microservices. They give a partner team a working, opinionated starting point
instead of a blank page.

The Enterprise RAG Blueprint is a production-ready, modular reference architecture for building
high-accuracy, high-performance retrieval systems that power enterprise search, knowledge assistants,
copilots, and agentic workflows, covering ingestion, retrieval, reasoning, and generation across
multimodal enterprise data. It implements agentic RAG as a plan-and-execute pipeline that treats a
query as something to reason about rather than a single retrieval call, which helps with multi-hop
questions, ambiguity, cross-document queries, and pulling numbers from tables or charts. It wires
together an LLM NIM with NeMo Retriever embedding and reranking NIMs. Other blueprints include AI-Q for
building agents that query and act on enterprise knowledge, a Data Flywheel blueprint that continuously
distills large models into smaller cheaper ones, and a Video Search and Summarization blueprint.

For an integrator, blueprints plus NVIDIA AI Enterprise are the fast path from a client requirement to
a supported, reusable accelerator: start from the blueprint, customize it for the client, and ship it
on a supported foundation, then reuse the pattern for the next client.

## Sources (NVIDIA)
- NVIDIA AI Enterprise: https://www.nvidia.com/en-us/data-center/products/ai-enterprise/
- NIM security lifecycle (NVAIE): https://docs.nvidia.com/ai-enterprise/planning-resource/ai-enterprise-security-white-paper/latest/nim-microservices.html
- Enterprise RAG / agentic RAG: https://docs.nvidia.com/rag/latest/agentic-rag.html
- RAG topic hub: https://developer.nvidia.com/topics/ai/retrieval-augmented-generation
- AI-Q research agent blueprint: https://docs.nvidia.com/enterprise-reference-architectures/ai-q-research-agent-blueprint/latest/introduction.html
