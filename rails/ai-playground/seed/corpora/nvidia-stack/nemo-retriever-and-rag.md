# NeMo Retriever and enterprise RAG

NVIDIA NeMo Retriever is an end-to-end, agent-ready stack that turns enterprise documents into a
structured knowledge layer that agents and RAG applications can reason over. It has three parts: the
open-source **NeMo Retriever Library** for ingestion and extraction, the **Nemotron Retriever** open
models, and the **NVIDIA NIM microservices** that serve those models with an API. The retrieval NIMs
run GPU-accelerated on CUDA, TensorRT, and the Triton Inference Server.

The NeMo Retriever Library is a GPU-accelerated ingestion framework that extracts text, tables,
charts, infographics, and transcripts from PDFs, HTML, Word, PowerPoint, audio, video, and images at
terabyte scale. Getting the content out of messy enterprise files is often the hardest and most
under-appreciated part of a production RAG system, so this stage is first-class rather than bespoke
glue. There is also a NeMo Retriever OCR NIM for reading text from images.

Two model stages sit at the heart of retrieval. **Embedding** converts document chunks and the user's
query into vector embeddings, stored in a GPU-accelerated vector database for fast indexing and
search; a current embedding model is llama-3.2-nv-embedqa-1b-v2 (multilingual), and the newer text
embedding NIM references nvidia/nemotron-3-embed-1b. **Reranking** then reorders the retrieved
candidates with a fine-tuned model so the passages most relevant to the query are the ones passed to
the LLM as context; a current reranker is nvidia/llama-nemotron-rerank-vl-1b-v2, a vision-language
model that can rank text, image, or combined passages. Reranking is especially valuable when merging
sources scored differently (for example cosine similarity versus BM25 keyword scores).

A reference enterprise RAG pipeline therefore has GPU-accelerated stages that each speak a standard
API and can be scaled or swapped independently: ingest and extract the corpus; embed chunks and the
query into vectors; retrieve candidates from the vector store; rerank them for relevance; and generate
a grounded, cited answer with an LLM. NVIDIA reports gains from this stack such as roughly 50 percent
fewer incorrect answers, 3x higher embedding throughput, 15x higher extraction throughput, and 35x
better storage efficiency versus prior approaches.

For agentic applications, the **NeMo Agent Toolkit** (a lightweight, framework-agnostic library, and
formerly the Agent Intelligence Toolkit) helps build, run, and improve agent workflows. It integrates
with orchestration frameworks like LangGraph, LlamaIndex, CrewAI, and Semantic Kernel, and supports
the Model Context Protocol (MCP) and Agent-to-Agent (A2A) protocol, so retrieval becomes one
composable tool among many an agent can call.

For a Global Systems Integrator developer, the win is that ingestion, embedding, reranking, and
generation are all supported microservices rather than infrastructure to build from scratch. The
integration work becomes wiring standard endpoints together and tuning retrieval quality, where
reranking is usually the highest-leverage improvement.

## Sources (NVIDIA)
- NeMo Retriever: https://developer.nvidia.com/nemo-retriever
- Text Embedding NIM: https://docs.nvidia.com/nim/nemo-retriever/text-embedding/latest/overview.html
- Text Reranking NIM: https://docs.nvidia.com/nim/nemo-retriever/text-reranking/latest/overview.html
- NeMo (agent-first suite): https://www.nvidia.com/en-us/ai-data-science/products/nemo/
- NeMo Agent Toolkit: https://docs.nvidia.com/nemo/agent-toolkit/latest/index.html
