"""Streamlit classroom demo for saved predictions or live local multi-image reranking."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Sequence

from docreranker.evaluation import evaluate, validate_predictions
from docreranker.inference import QwenReranker, candidate_page_ids, input_fingerprint, read_jsonl


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Candidate JSONL")
    parser.add_argument("--predictions", type=Path, help="Optional genuine model predictions to replay")
    parser.add_argument("--model", default="Qwen/Qwen2.5-VL-7B-Instruct")
    parser.add_argument("--adapter", default="")
    parser.add_argument("--device", default="auto")
    return parser


def app(args: argparse.Namespace) -> None:
    import streamlit as st

    st.set_page_config(page_title="DocReranker", layout="wide")
    st.title("DocReranker · Visual document reranking")
    st.caption("Question → retrieve candidate pages → read page images → rerank by evidence")
    with st.sidebar:
        st.header("Input data")
        input_path = st.text_input("Candidate page JSONL", str(args.input or ""))
        prediction_path = st.text_input("Saved prediction JSONL", str(args.predictions or ""))
        mode = st.radio("Mode", ["Replay predictions", "Live reranking"])
        show_gold = st.checkbox("Show dataset relevance labels", value=False)
        limit = st.slider("Number of pages to display", 1, 20, 5)
    if not input_path:
        st.info("Enter a candidate JSONL path. Replay also requires a genuine model prediction file.")
        return
    try:
        records = read_jsonl(input_path)
        for candidate_record in records:
            candidate_page_ids(candidate_record)
        if not records:
            st.info("The candidate file contains no questions.")
            return
    except (OSError, ValueError) as exc:
        st.error(str(exc))
        return
    selected_id = st.selectbox("Select a question", [record["query_id"] for record in records])
    record = next(record for record in records if record["query_id"] == selected_id)
    query = st.text_area("Question", value=record["query"], key=f"query:{selected_id}", disabled=mode == "Replay predictions")
    prediction = None
    report = None
    if mode == "Replay predictions":
        if not prediction_path:
            st.info("Enter a genuine prediction file to display the reranked pages.")
        else:
            try:
                predictions = read_jsonl(prediction_path)
                prediction = validate_predictions(records, predictions)[selected_id]
                report = evaluate(records, predictions)
                st.caption(f"Prediction file: {prediction_path} · Model: {prediction.get('model', 'model not recorded')}")
                st.caption(f"LoRA adapter: {prediction.get('adapter') or 'none'}")
            except (OSError, ValueError) as exc:
                st.error(f"Cannot match predictions to candidates: {exc}")
    else:
        with st.sidebar:
            st.header("Local model")
            model_name = st.text_input("Model / merged checkpoint", args.model)
            adapter = st.text_input("LoRA adapter (optional)", args.adapter)
            device = st.text_input("Device", args.device)
            visual_tokens = st.slider("Maximum visual tokens per page", 256, 1280, 1024, 128)
        edited_record = {**record, "query": query}
        key = (input_fingerprint(edited_record), model_name, adapter, device, visual_tokens)
        if query != record["query"]:
            show_gold = False
            st.caption("Editing the question reuses its existing candidates and reruns only reranking. The original relevance labels are hidden.")

        @st.cache_resource(show_spinner="Loading the visual model…")
        def load_reranker(model_name: str, adapter: str, device: str, visual_tokens: int):
            return QwenReranker(model_name, adapter=adapter or None, device=device, max_pixels=visual_tokens * 28 * 28)

        if st.button("Rerank page images", type="primary", disabled=not query.strip()):
            try:
                with st.spinner("Reading page images and generating an order…"):
                    reranker = load_reranker(model_name, adapter, device, visual_tokens)
                    st.session_state["live_result"] = (key, reranker.rerank(edited_record))
            except Exception as exc:
                st.error(f"Inference failed: {type(exc).__name__}: {exc}")
        cached = st.session_state.get("live_result")
        if cached and cached[0] == key:
            prediction = cached[1]

    candidates = {page["page_id"]: page for page in record["candidates"]}
    baseline_ids = list(candidates)
    original_indices = {page_id: index for index, page_id in enumerate(baseline_ids, 1)}
    gold = set(record.get("positive_page_ids", []))
    before, after = st.columns(2)

    def show_pages(column, title: str, ranking: list[str]) -> None:
        with column:
            st.subheader(title)
            if not ranking:
                st.caption("No candidate pages.")
            for rank, page_id in enumerate(ranking[:limit], 1):
                page = candidates[page_id]
                label = f"#{rank} · {page_id} · Input page {original_indices[page_id]}"
                if show_gold:
                    label += " · relevant" if page_id in gold else " · irrelevant"
                st.markdown(f"**{label}**")
                if "score" in page:
                    st.caption(f"Retrieval score: {page['score']}")
                try:
                    st.image(page["image"], use_container_width=True)
                except Exception as exc:
                    st.error(f"Cannot display page: {exc}")

    show_pages(before, "Retrieval order", baseline_ids)
    if prediction:
        show_pages(after, "Reranked order", prediction["ranked_page_ids"])
        if prediction.get("fallback"):
            st.warning(f"The model output was not a valid complete permutation. Retaining retrieval order. Reason: {prediction.get('fallback_reason', 'invalid format')}")
        response = prediction.get("raw_response", "")
        evidence = re.search(r"<think>(.*?)</think>", response, flags=re.DOTALL)
        st.subheader("Generated page evidence summary")
        st.write(evidence.group(1).strip() if evidence else "The prediction file contains no evidence summary.")
        st.caption(f"Inference batch time: {prediction.get('elapsed', 0):.2f} seconds · Questions in batch: {prediction.get('batch_size', 1)}")
        with st.expander("View raw output"):
            st.code(response, language="text")
        st.download_button("Download this prediction", json.dumps(prediction, ensure_ascii=False) + "\n",
                           file_name="prediction.jsonl", mime="application/jsonl")
    else:
        with after:
            st.subheader("Reranked order")
            st.info("Load saved predictions or run live inference to display an order.")
    if report:
        with st.expander("Evaluation of the loaded predictions"):
            st.caption(f"Evaluated questions: {report['evaluated_queries']} · Skipped without gold: {report['skipped_no_gold']} · Format fallbacks: {report['fallback_queries']}")
            st.table([{"Metric": name, "Retrieval": report["baseline"][name], "Reranker": value,
                       "Change": report["delta"][name]} for name, value in report["reranker"].items()])


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
    except ImportError as exc:
        raise SystemExit("Install the demo extras first: pip install -e '.[demo]'") from exc
    if get_script_run_ctx(suppress_warning=True) is None:
        forwarded = list(argv) if argv is not None else sys.argv[1:]
        raise SystemExit(subprocess.call([sys.executable, "-m", "streamlit", "run", str(Path(__file__).resolve()), "--", *forwarded]))
    app(args)


if __name__ == "__main__":
    main()
