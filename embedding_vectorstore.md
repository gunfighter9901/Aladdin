---
name: Qdrant + FastEmbed RAG Architecture
description: A high-level guide and minimal implementation template for setting up a local Retrieval-Augmented Generation (RAG) vector storage stack. This setup leverages Qdrant and FastEmbed with the BAAI/bge-small-en-v1.5 model, specifically optimized with Memory Mapping (MMap) and Binary Quantization (BQ) for efficient, local execution.
---

# RAG Chunking & Vector Storage Strategy

## 1. Core Architecture
* **Embedding Model:** `BAAI/bge-small-en-v1.5` (via FastEmbed for fast, local CPU-based embeddings).
* **Vector Database:** Qdrant (Local persistent storage).

## 2. Chunking & Metadata Strategy
* **Chunking:** Utilize recursive character splitting (e.g., 500-token chunks with 50-token overlap) to preserve context.
* **Metadata Context:** Always attach rich metadata (`source_file`, `chunk_index`, `category`, `timestamp`) to each chunk. This is critical for post-retrieval filtering and hybrid search.

## 3. Storage Optimization (Qdrant)
* **Memory Mapping (MMap):** Utilizing Qdrant's disk-based persistence (`on_disk_payload=True`) ensures MMap is used efficiently, allowing the system to handle datasets larger than available RAM.
* **Binary Quantization (BQ):** Enabled during collection creation to compress vectors up to 32x and heavily accelerate retrieval speeds, which works exceptionally well with models like `bge-small-en-v1.5`.

## 4. Minimal Implementation
note:- this below code is only for reference
```python
from qdrant_client import QdrantClient
from qdrant_client.models import BinaryQuantization, BinaryQuantizationConfig

# 1. Initialize Local Qdrant and set FastEmbed Model
client = QdrantClient(path="./local_qdrant_db")
client.set_model("BAAI/bge-small-en-v1.5")

# 2. Create Collection with BQ and MMap configurations
client.create_collection(
    collection_name="rag_knowledge_base",
    vectors_config=client.get_fastembed_vector_params(),
    quantization_config=BinaryQuantization(
        binary=BinaryQuantizationConfig(always_ram=True) # Binary Quantization
    ),
    on_disk_payload=True  # Enables Memory Mapping (MMap) for payloads
)

# 3. Insert Chunks with Metadata
client.add(
    collection_name="rag_knowledge_base",
    documents=[
        "Qdrant is a vector similarity search engine.",
        "FastEmbed provides lightweight, fast embedding generation."
    ],
    metadata=[
        {"name": "qdrant_docs", "description": "Official documentation for Qdrant vector DB", "source": "qdrant_docs.pdf", "chunk_index": 0},
        {"name": "fastembed_docs", "description": "Official guide for FastEmbed local embeddings", "source": "fastembed_docs.pdf", "chunk_index": 1}
    ]
)
```
