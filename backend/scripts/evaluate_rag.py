"""Evaluate local RAG with Opik native metrics.

Runs the same retriever + Gemini generator the ReAct agent uses
(`app.vectorstore.get_store().search` + `app.gemini_client.get_gemini()`)
against golden_dataset.jsonl, then scores with:

- Hallucination, AnswerRelevance, ContextRecall, ContextPrecision

Usage:
    cd backend
    python scripts/evaluate_rag.py --samples 5
    python scripts/evaluate_rag.py --dataset-name Rag_eval --experiment-name educator-rag-eval

Keys come from backend/.env (GEMINI_API_KEY, OPIK_API_KEY, OPIK_WORKSPACE,
OPIK_PROJECT_NAME, OPIK_URL_OVERRIDE). No new variables. Never pass keys as flags.
Judges use Gemini 3.5 Flash Lite via LiteLLM with the same GEMINI_API_KEY
(same as the tutor reasoning model; 2.5-flash-lite returns 404 for this key).
Override with --judge-model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

REPO_ROOT = BACKEND_DIR.parent
DEFAULT_GOLDEN = REPO_ROOT / "golden_dataset.jsonl"
DEFAULT_JUDGE_MODEL = "gemini/gemini-3.5-flash-lite"

from app.config import settings  # noqa: E402  (loads backend/.env)


def load_golden(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        user_input = (item.get("user_input") or item.get("input") or "").strip()
        reference = (item.get("reference") or item.get("expected_output") or "").strip()
        if not user_input or not reference:
            continue
        rows.append({"input": user_input, "expected_output": reference})
    if not rows:
        raise ValueError(f"No usable items in {path} (need user_input + reference).")
    return rows


def sync_dataset(client, name: str, rows: list[dict]):
    dataset = client.get_or_create_dataset(name=name)
    try:
        existing = dataset.get_items(nb_samples=1)
    except Exception:
        existing = []
    if not existing:
        dataset.insert(rows)
        print(f"Uploaded {len(rows)} items to dataset '{name}'.")
    else:
        print(f"Dataset '{name}' already has items; skipping upload (deduplication on).")
    return dataset


def get_ready_source_ids() -> list[str]:
    from sqlmodel import select

    from app.database import engine
    from app.models import Source
    from sqlmodel import Session

    with Session(engine) as db:
        rows = db.exec(select(Source).where(Source.status == "ready")).all()
        return [row.id for row in rows if row.id]


def run_rag(question: str, source_ids: list[str], limit: int) -> tuple[str, list[str]]:
    import time

    from app.errors import GeminiUnavailable
    from app.gemini_client import get_gemini
    from app.vectorstore import get_store

    hits = get_store().search(question, list(source_ids or []), limit) if source_ids else []
    contexts = [hit.text for hit in hits if hit.text and hit.text.strip()]
    if contexts:
        joined = "\n\n".join(f"[Chunk {i + 1}]\n{t[:2500]}" for i, t in enumerate(contexts))
        prompt = (
            "Answer the student's question using only the context below. "
            "Be concise and factual. If the context does not contain the answer, say so.\n\n"
            f"Context:\n{joined}\n\nQuestion: {question}"
        )
    else:
        prompt = (
            "Answer the student's question concisely and factually.\n\n"
            f"Question: {question}"
        )
    # Free-tier quota is 15 req/min; a task failure aborts the whole run,
    # so back off and retry on rate limits instead of failing fast.
    answer = ""
    for attempt in range(4):
        try:
            answer = asyncio.run(get_gemini().generate_text(prompt, temperature=0.2))
            break
        except GeminiUnavailable as exc:
            if "rate-limit" not in str(exc).lower() or attempt == 3:
                raise
            time.sleep(65)
    return answer.strip(), contexts


def build_task(source_ids: list[str], limit: int):
    import time

    from opik import track

    @track(name="rag_eval_task", tags=["educator", "eval"])
    def evaluation_task(dataset_item: dict) -> dict:
        # Pace items so 5 calls/item (1 answer + 4 judges) stays under the
        # 15 req/min free-tier quota.
        time.sleep(15)
        question = (dataset_item.get("input") or dataset_item.get("user_input") or "").strip()
        expected = (dataset_item.get("expected_output") or dataset_item.get("reference") or "").strip()
        if not question:
            raise ValueError("Dataset item has no input/user_input.")
        output, context = run_rag(question, source_ids, limit)
        return {
            "input": question,
            "output": output,
            "context": context,
            "expected_output": expected,
        }

    return evaluation_task


def build_metrics(judge_model: str | None):
    from opik.evaluation.metrics import (
        AnswerRelevance,
        ContextPrecision,
        ContextRecall,
        Hallucination,
    )

    if judge_model:
        return [
            Hallucination(model=judge_model),
            AnswerRelevance(model=judge_model),
            ContextRecall(model=judge_model),
            ContextPrecision(model=judge_model),
        ]
    return [Hallucination(), AnswerRelevance(), ContextRecall(), ContextPrecision()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate local RAG with Opik metrics.")
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN), help="Path to golden_dataset.jsonl")
    parser.add_argument("--dataset-name", default="Rag_eval")
    parser.add_argument("--experiment-name", default="educator-rag-eval")
    parser.add_argument("--samples", type=int, default=None, help="Evaluate first N items (default: all)")
    parser.add_argument("--task-threads", type=int, default=1)
    parser.add_argument("--retrieval-limit", type=int, default=settings.retrieval_limit)
    parser.add_argument(
        "--judge-model",
        default=DEFAULT_JUDGE_MODEL,
        help=f"LiteLLM judge model (default: {DEFAULT_JUDGE_MODEL})",
    )
    args = parser.parse_args()

    if not settings.gemini_configured():
        raise SystemExit("GEMINI_API_KEY is missing in backend/.env; cannot generate answers.")
    if not settings.opik_configured():
        raise SystemExit("OPIK_API_KEY (or OPIK_URL_OVERRIDE) is missing in backend/.env; cannot log eval.")

    golden_path = Path(args.golden)
    if not golden_path.is_file():
        raise SystemExit(f"Golden file not found: {golden_path}")
    rows = load_golden(golden_path)
    print(f"Loaded {len(rows)} golden items from {golden_path.name}.")

    from opik import Opik
    from opik.evaluation import evaluate

    client = Opik()
    dataset = sync_dataset(client, args.dataset_name, rows)

    source_ids = get_ready_source_ids()
    print(f"Ready sources: {len(source_ids)} (empty RAG context scores low; ingest sources first).")

    task = build_task(source_ids, max(1, min(int(args.retrieval_limit or 6), 20)))
    judge_model = (args.judge_model or DEFAULT_JUDGE_MODEL).strip()
    metrics = build_metrics(judge_model)

    # `import litellm` (via the judge models above) writes an empty
    # OPIK_URL_OVERRIDE into os.environ, which makes Opik clients built
    # afterwards use a relative URL. Drop it, same guard as app.config.
    if not os.environ.get("OPIK_URL_OVERRIDE", "").strip():
        os.environ.pop("OPIK_URL_OVERRIDE", None)

    results = evaluate(
        experiment_name=args.experiment_name,
        dataset=dataset,
        task=task,
        scoring_metrics=metrics,
        nb_samples=args.samples,
        task_threads=max(1, int(args.task_threads or 1)),
        experiment_config={
            "retrieval_limit": int(args.retrieval_limit or 6),
            "reasoning_model": settings.reasoning_model,
            "ready_sources": len(source_ids),
            "judge_model": judge_model,
        },
    )
    print(results)


if __name__ == "__main__":
    main()
