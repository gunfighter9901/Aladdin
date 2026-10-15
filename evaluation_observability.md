---
title: "Agentic RAG Observability & Evaluation Blueprint"
description: "High-level integration patterns and architectural separation between Opik and Ragas for agent systems."
version: "1.1.0"
category: "AI Engineering / MLOps"
tools:
  - name: "Opik"
    role: "Observability, trace recording, dataset storage, experiment UI"
  - name: "Ragas"
    role: "Evaluation metrics engine, LLM-as-a-judge scoring"
target_audience: "AI Engineers, Agent Architects"
last_updated: "2026-10-13"
---

# Blueprint: Evaluating & Observing Agentic RAG using Opik + Ragas

This guide outlines the standard architectural pattern for combining **Opik** (for LLM observability, tracing, and experiment management) with **Ragas** (for specialized, research-backed RAG metrics via the `RagasMetricWrapper`).

By implementing this architecture, the agent system gains an automated flight recorder (Opik) equipped with an advanced evaluation engine (Ragas).

---

## 🧭 When to Use: Observation vs. Evaluation

A common architectural trap is treating observation and evaluation as the same task. They address different lifecycle stages:

| Dimension | Observation (Opik) | Evaluation (Ragas via Wrapper) |
| :--- | :--- | :--- |
| **Primary Goal** | **Transparency & Health:** Monitor *what happened* step by step. | **Quality & Accuracy:** Measure *how good* the output and context are. |
| **Trigger Point** | Continuous / Always on (100% live traffic or wide telemetry). | Event-driven: CI/CD pull requests, scheduled offline runs, or sampled traffic audits. |
| **Typical Questions Answered** | - Which tool failed or timed out?<br>- Where is latency accumulating?<br>- How many tokens were spent per run? | - Did the agent hallucinate facts?<br>- Was the retrieved context relevant?<br>- Did the response answer the user's intent? |
| **Cost Profile** | **Low:** Telemetry and logging overhead only (no extra LLM calls). | **Moderate to High:** Invokes secondary LLMs (LLM-as-a-judge) to calculate scores. |

### When to trigger Observation (Opik)
* **Live Production Monitoring:** Record inputs, tool calls, retrieved chunks, and final agent responses for troubleshooting.
* **Latency & Token Budgeting:** Identify slow nodes or expensive prompts inside multi-step agent chains.
* **Debugging Failures:** Inspect the exact payload that caused an agent loop to crash or produce poor results.

### When to trigger Evaluation (Ragas Wrapper)
* **Pre-Deployment / CI/CD:** Run a golden dataset before releasing prompt changes, new retriever models, or tool configurations.
* **Retrieval Quality Audits:** Specifically score *Context Precision* and *Context Recall* to validate chunking and vector search logic.
* **Offline Batch Audits:** Run periodic checks on a 5–10% sample of production traces to catch silent quality drift without incurring real-time inference costs.

---

## 🏗️ Core Architecture & Responsibility Split

In a production-ready agentic RAG application, the two tools split responsibilities to avoid system bloat:

1. **Opik (The Infrastructure & Logger):** Acts as the central nervous system. It records agent trace trajectories, manages test datasets, hosts prompt playgrounds, and provides the visual UI dashboard.
2. **Ragas (The Scoring Analyst):** Acts as the evaluation brain. Wrapped cleanly inside Opik, it takes the execution traces captured by Opik and applies mathematical algorithms and LLM-as-a-judge prompts to score your retrieval, context, and response quality.