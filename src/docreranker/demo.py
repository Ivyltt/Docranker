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
    st.title("DocReranker · 多模态文档重排序")
    st.caption("问题 → 检索候选页面 → 多模态模型阅读页面 → 按证据重新排序")
    with st.sidebar:
        st.header("演示数据")
        input_path = st.text_input("候选页面 JSONL", str(args.input or ""))
        prediction_path = st.text_input("已生成的预测 JSONL", str(args.predictions or ""))
        mode = st.radio("模式", ["回放模型预测", "现场重排序"])
        show_gold = st.checkbox("展示数据集相关性标签", value=False)
        limit = st.slider("展示前几页", 1, 20, 5)
    if not input_path:
        st.info("填写候选页面 JSONL 的路径。回放模式还需要实际模型生成的预测文件。")
        return
    try:
        records = read_jsonl(input_path)
        for candidate_record in records:
            candidate_page_ids(candidate_record)
        if not records:
            st.info("候选数据文件没有查询。")
            return
    except (OSError, ValueError) as exc:
        st.error(str(exc))
        return
    selected_id = st.selectbox("选择查询", [record["query_id"] for record in records])
    record = next(record for record in records if record["query_id"] == selected_id)
    query = st.text_area("问题", value=record["query"], key=f"query:{selected_id}", disabled=mode == "回放模型预测")
    prediction = None
    report = None
    if mode == "回放模型预测":
        if not prediction_path:
            st.info("填写真实预测文件后即可显示重排序结果。")
        else:
            try:
                predictions = read_jsonl(prediction_path)
                prediction = validate_predictions(records, predictions)[selected_id]
                report = evaluate(records, predictions)
                st.caption(f"回放文件：{prediction_path} · 模型：{prediction.get('model', '文件未记录模型')}")
                st.caption(f"LoRA adapter：{prediction.get('adapter') or '无'}")
            except (OSError, ValueError) as exc:
                st.error(f"预测与候选数据无法核对：{exc}")
    else:
        with st.sidebar:
            st.header("本地模型")
            model_name = st.text_input("模型 / merged checkpoint", args.model)
            adapter = st.text_input("LoRA adapter（可留空）", args.adapter)
            device = st.text_input("设备", args.device)
            visual_tokens = st.slider("每页最多视觉 tokens", 256, 1280, 1024, 128)
        edited_record = {**record, "query": query}
        key = (input_fingerprint(edited_record), model_name, adapter, device, visual_tokens)
        if query != record["query"]:
            show_gold = False
            st.caption("当前复用所选查询的候选页面；修改问题后只运行重排序，原查询的相关性标签不参与展示。")

        @st.cache_resource(show_spinner="加载多模态模型…")
        def load_reranker(model_name: str, adapter: str, device: str, visual_tokens: int):
            return QwenReranker(model_name, adapter=adapter or None, device=device, max_pixels=visual_tokens * 28 * 28)

        if st.button("运行多图重排序", type="primary", disabled=not query.strip()):
            try:
                with st.spinner("读取页面并生成排序…"):
                    reranker = load_reranker(model_name, adapter, device, visual_tokens)
                    st.session_state["live_result"] = (key, reranker.rerank(edited_record))
            except Exception as exc:
                st.error(f"推理未完成：{type(exc).__name__}: {exc}")
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
                st.caption("没有候选页面。")
            for rank, page_id in enumerate(ranking[:limit], 1):
                page = candidates[page_id]
                label = f"#{rank} · {page_id} · 输入 Page {original_indices[page_id]}"
                if show_gold:
                    label += " · 相关" if page_id in gold else " · 不相关"
                st.markdown(f"**{label}**")
                if "score" in page:
                    st.caption(f"检索分数：{page['score']}")
                try:
                    st.image(page["image"], use_container_width=True)
                except Exception as exc:
                    st.error(f"无法显示页面：{exc}")

    show_pages(before, "检索结果", baseline_ids)
    if prediction:
        show_pages(after, "重排序结果", prediction["ranked_page_ids"])
        if prediction.get("fallback"):
            st.warning(f"模型未输出合法的完整排序，已保留检索顺序。原因：{prediction.get('fallback_reason', '格式错误')}")
        response = prediction.get("raw_response", "")
        evidence = re.search(r"<think>(.*?)</think>", response, flags=re.DOTALL)
        st.subheader("模型生成的页面证据摘要")
        st.write(evidence.group(1).strip() if evidence else "预测文件未提供证据摘要。")
        st.caption(f"推理批次耗时：{prediction.get('elapsed', 0):.2f} 秒 · 批次查询数：{prediction.get('batch_size', 1)}")
        with st.expander("查看原始输出"):
            st.code(response, language="text")
        st.download_button("下载此查询的预测", json.dumps(prediction, ensure_ascii=False) + "\n",
                           file_name="prediction.jsonl", mime="application/jsonl")
    else:
        with after:
            st.subheader("重排序结果")
            st.info("加载预测文件或运行现场推理后显示。")
    if report:
        with st.expander("该预测文件的真实评估结果"):
            st.caption(f"有效查询 {report['evaluated_queries']} · 无 gold 跳过 {report['skipped_no_gold']} · 格式回退 {report['fallback_queries']}")
            st.table([{"指标": name, "检索": report["baseline"][name], "重排序": value,
                       "变化": report["delta"][name]} for name, value in report["reranker"].items()])


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
