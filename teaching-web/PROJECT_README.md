# DocReranker

Run a visual document reranking project: retrieve pages with ColQwen2, create supervised examples with Gemini, fine-tune Qwen2.5-VL with LoRA, merge the SFT adapter, and improve ranking with GRPO.

[Interactive project walkthrough](https://docreranker-classroom.litong1812.chatgpt.site) · [Source code](src/docreranker/) · [Recorded GRPO configuration](experiment/grpo-config.json)

## 1. Install and run your first example

Use Python **3.11**. The commands below assume Linux or a Linux terminal such as WSL. Run every command from the repository root, the directory containing this README.

```bash
git clone https://github.com/Ivyltt/Docranker.git
cd Docranker
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python scripts/teaching_example.py --output outputs/first-example.json
```

This example needs **no GPU, downloaded dataset, model weights, or API key**. It checks ranking metrics and GRPO rewards using five synthetic candidates. Expect `"hand_calculation_checks": "passed"`; open `outputs/first-example.json` to inspect the calculation. These are practice numbers, not the experimental results.

If `python3.11` is unavailable, install Python 3.11 before creating the environment. On Windows PowerShell, activate it with `.venv\Scripts\Activate.ps1` instead of `source`.

Install the remaining dependencies when you are ready for real data and GPU work:

```bash
python -m pip install -e '.[data,train,demo,dev]'
python -c "import torch; print('CUDA available:', torch.cuda.is_available()); print('GPUs:', torch.cuda.device_count())"
```

`data` installs the Hugging Face downloader and Parquet reader. `train` installs the pinned PyTorch, Transformers, TRL, PEFT, and Accelerate versions. `demo` installs Streamlit. CPU preparation works without CUDA; model inference and training require an allocated NVIDIA GPU. The full experiment used H100 80 GB GPUs. Large Parquet conversion was run with 64 GB of CPU RAM. On a university cluster, obtain a CPU/GPU allocation using your cluster's instructions before the corresponding steps.

## 2. Understand what you will build

In RAG, retrieved evidence is added to a question before a model generates an answer. This project implements the **page retrieval and reranking** portion. It outputs an evidence-page order; it does not implement a final answer generator.

```text
Question + document page images
  -> ColQwen2 scores pages and retrieves up to five candidates
  -> Qwen reads the question and the real candidate images
  -> Qwen returns a complete ordering, for example [2, 1, 4, 3, 5]
  -> Evaluate the ordering against the dataset's relevant-page IDs
```

The MMDocIR task supplies the document or documents to search. Retrieval searches their pages. Five is our candidate budget: it balances evidence coverage with the cost of reading several page images. It is not the number of correct pages. The reranker cannot recover a relevant page that retrieval omitted.

**Labels come from MMDocIR, not ColQwen2.** ColQwen2 provides similarity scores and an initial order. It encodes page images as multiple vectors and uses late interaction: for each query vector, find its best-matching page vector, then sum those matches (MaxSim). This preserves visual evidence in tables, figures, and layouts without an OCR-first representation. Page vectors can be cached. We use it as a fixed, publicly available visual retrieval baseline; this project does not establish that it outperforms every other document encoder.

## 3. Download the datasets

The download commands fetch the official files directly; do not use `datasets.load_dataset()` on the mixed training schemas.

| Resource | Official download page | Local destination |
| --- | --- | --- |
| MMDocIR training annotations and page images | [MMDocIR_Train_Dataset](https://huggingface.co/datasets/MMDocIR/MMDocIR_Train_Dataset/tree/main) | `data/raw/train/` |
| MMDocIR evaluation annotations and page images | [MMDocIR_Evaluation_Dataset](https://huggingface.co/datasets/MMDocIR/MMDocIR_Evaluation_Dataset/tree/main) | `data/raw/evaluation/` |

First inspect the download size. These commands contact Hugging Face but do not download the dataset files:

```bash
python -m docreranker.data download --split train --output data/raw/train
python -m docreranker.data download --split evaluation --output data/raw/evaluation
```

The training artifacts used by the project are about 49 GB; the evaluation annotations and page Parquet are about 1.6 GB. The displayed `bytes` field gives the size at the resolved dataset revision. Allow additional space for extracted PNGs, model weights, cached embeddings, and checkpoints.

For the training workflow, run this preparation command on a CPU machine with sufficient memory:

```bash
python scripts/prepare_training_data.py \
  --limit-queries 10000 --minimum-train-queries 7200 \
  --image-workers 4 --seed 42
```

This command **downloads both datasets**, extracts real page images, selects questions, splits training and validation by document, and excludes training questions/documents overlapping the official evaluation set. It writes:

```text
data/raw/train/
  annotations_top1_negative/<subset>_train.jsonl
  parquet/<subset>_filter.parquet
  download_manifest.json
data/raw/evaluation/
  MMDocIR_annotations.jsonl
  MMDocIR_pages.parquet
  download_manifest.json
data/train/
  images/*.png
  pages.jsonl
  queries.jsonl
data/splits/
  train_queries.jsonl
  train_pages.jsonl
  validation_queries.jsonl
  validation_pages.jsonl
  manifest.json
data/evaluation/
  images/*.png
  pages.jsonl
  queries.jsonl
```

The seven training subsets are ArxivQA, DUDE, MP-DocVQA, SciQAG, SlideVQA, TAT-DQA, and Wiki-ss. Read `outputs/training-data-preparation/status.json` for progress and the final status. Downloads and content-addressed image files can be reused when rerunning the same preparation settings. Keep the same selection settings after preparation has started; the script rejects changes to a frozen selection.

**For a retrieval-only first run**, download just the evaluation files and extract 20 questions instead:

```bash
python -m docreranker.data download --split evaluation \
  --output data/raw/evaluation --execute
python -m docreranker.data convert --split evaluation \
  --raw data/raw/evaluation --output data/evaluation-small \
  --limit-queries 20 --image-workers 4
```

Use `data/evaluation-small/` instead of `data/evaluation/` in Steps 5 and 8 for this small run. It still scans the source Parquet. To train later, run the full preparation command above.

### What a data row means

A raw training annotation contains a question and `positive_passages` with document names and page numbers. The corresponding Parquet contains the page images. Conversion produces these two kinds of JSONL records (illustrative values):

```json
{"query_id":"train:ArxivQA:0","query":"Which curve has the highest value?","subset":"ArxivQA","split":"train","doc_names":["paper"],"positive_page_ids":["[\"ArxivQA\",\"paper\",2]"]}
```

```json
{"page_id":"[\"ArxivQA\",\"paper\",2]","doc_name":"paper","page_number":2,"subset":"ArxivQA","image":"/absolute/path/Docranker/data/train/images/example.png","width":1000,"height":1400}
```

`positive_page_ids` lists **all** relevant pages. An image is an actual extracted PNG. A global page ID encodes `[subset, document, page]`; a model answer uses **local positions 1 through K** in the supplied image list. Do not interchange them. Evaluation page numbers are zero-based offsets within the annotation's inclusive `page_indices` range; the converter handles this mapping. Image paths are absolute: if you move the project, regenerate the converted metadata before continuing.

## 4. Download the two models

No project-trained adapter or merged checkpoint is included in this repository. The commands below download public starting models; Steps 6–7 create your own trained reranker.

| Model | Purpose | Local destination |
| --- | --- | --- |
| [vidore/colqwen2-v1.0-hf](https://huggingface.co/vidore/colqwen2-v1.0-hf) | Encode and score page images for retrieval | `models/colqwen2/` |
| [Qwen/Qwen2.5-VL-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct) | Base visual reranker, then LoRA training | `models/qwen2.5-vl-7b/` |

```bash
hf download vidore/colqwen2-v1.0-hf --local-dir models/colqwen2
hf download Qwen/Qwen2.5-VL-7B-Instruct --local-dir models/qwen2.5-vl-7b
```

Wait for both downloads to finish. Keep the tokenizer and processor files together with the weights; copying only `.safetensors` files is insufficient. The directories should contain `config.json`, processor/tokenizer files, and model weight files. You do not need to clone either model repository with Git.

## 5. Retrieve five candidate pages

Run these commands on a GPU. The index stores ColQwen2 page vectors; search encodes questions and scores the cached pages.

For evaluation, preserve the actual retrieved candidates:

```bash
python -m docreranker.retrieval index \
  --pages data/evaluation/pages.jsonl --output data/index/evaluation \
  --model models/colqwen2 --batch-size 4 --device cuda
python -m docreranker.retrieval search \
  --queries data/evaluation/queries.jsonl --index data/index/evaluation \
  --output data/evaluation_candidates.jsonl --top-k 5 --device cuda
```

For SFT supervision, include known positive pages and the highest-scoring negative pages:

```bash
python scripts/retrieve_candidates.py \
  --pages data/splits/train_pages.jsonl \
  --queries data/splits/train_queries.jsonl \
  --output data/train_candidates.jsonl --index data/index/train --training
```

The helper uses `models/colqwen2/` and top-K=5. Each output row adds `candidates`, containing `page_id`, `image`, `score`, and `relevant`. `relevant` is computed from the dataset's gold IDs. The `--training` flag allows gold insertion for SFT data construction. **Never add it to evaluation, validation, or the GRPO retrieval below.** All gold IDs remain in evaluation metadata, including ones retrieval missed.

For an initial GPU check, keep only 20 candidate records:

```bash
python -c "from docreranker.io import read_jsonl, write_jsonl; write_jsonl('data/evaluation_demo.jsonl', read_jsonl('data/evaluation_candidates.jsonl')[:20])"
```

## 6. Create supervised examples with Gemini

Gemini is accessed through OpenRouter, not downloaded as a local model. Create an account at [OpenRouter](https://openrouter.ai/), add credits, and create a key on the [API keys page](https://openrouter.ai/settings/keys). Copy the template and edit `.env` to replace the placeholder with your key:

```bash
cp .env.example .env
```

For each question, Gemini receives **the question + one real page image + that page's positive/negative label**, generating a short evidence note per page. A second request receives **only the text notes, question, and preferred order**, producing a concise combined explanation. This separates visual inspection from text refinement and reduces repetitive or inconsistent notes. Code builds the final valid permutation from labels; Gemini does not invent the gold labels.

Preview a 16-example annotation run without paid requests:

```bash
python -m docreranker.annotation \
  --input data/train_candidates.jsonl --output data/sft.jsonl \
  --cache-dir outputs/annotation --model google/gemini-2.5-flash-lite \
  --limit 16 --skip-invalid-examples
```

Then execute that small run. This sends paid API requests; the local cumulative cap is $1 for this cache directory:

```bash
python -m docreranker.annotation \
  --input data/train_candidates.jsonl --output data/sft.jsonl \
  --cache-dir outputs/annotation --model google/gemini-2.5-flash-lite \
  --limit 16 --workers 4 --max-attempts 5 --skip-invalid-examples \
  --env-file .env --max-cost-usd 1 --execute
```

Inspect `data/sft.jsonl`: check that notes describe visible page evidence and that targets contain every candidate position exactly once. The cache and accounting records are in `outputs/annotation/`. To expand to 7,200 successful examples, rerun with `--limit 7200 --max-cost-usd 16` after inspecting a new dry-run estimate. Keep the same cache directory to reuse completed requests; the cap is cumulative across runs. Invalid teacher outputs are skipped and recorded; insufficient eligible examples cause an error rather than silently producing the requested count.

The generated SFT file stores `messages`, the ordered real `images`, and `positive_ids`. Candidate order is shuffled deterministically. **The Qwen student receives the question and all real candidate images; it does not receive the relevance labels or ColQwen2 scores.** Its target is:

```text
<think>Short, supervised evidence explanation.</think><answer>[2, 1, 4, 3, 5]</answer>
```

The explanation is a supervised evidence summary. SFT learns to predict the target tokens; the labels also enable data checks. Image inputs remain part of student training after Gemini's text refinement.

## 7. Train SFT and merge its LoRA adapter

First validate images, labels, targets, and configuration without loading a model:

```bash
python -m docreranker.training.sft \
  --config configs/sft.json --data data/sft.jsonl \
  --model models/qwen2.5-vl-7b --output outputs/sft \
  --exclude-queries data/evaluation/queries.jsonl --dry-run
```

Remove `--dry-run` to train on one GPU:

```bash
python -m docreranker.training.sft \
  --config configs/sft.json --data data/sft.jsonl \
  --model models/qwen2.5-vl-7b --output outputs/sft \
  --exclude-queries data/evaluation/queries.jsonl
```

The configuration uses LoRA rank 8 and alpha 32, learning rate `1e-4`, one epoch, batch size 1, and gradient accumulation 8. LoRA trains small low-rank weight updates while the base weights and vision encoder remain frozen. Loss is applied to the assistant target, not the input prompt. Sixteen examples test the pipeline; 7,200 examples at effective batch 8 give 900 updates. A small run will not reproduce the reported SFT score.

`outputs/sft/` contains the adapter weights, adapter configuration, processor files, and `docreranker_training.json`. Merge the adapter into the base model before the main SFT evaluation and before GRPO:

```bash
python -m docreranker.training.merge \
  --adapter outputs/sft --base-model models/qwen2.5-vl-7b \
  --output outputs/sft-merged --dtype bfloat16 --device cpu
```

Merging adds the trained LoRA update to the base weights; it is not another training stage. CPU merging needs memory for the full model; `--device cuda` uses a GPU instead. The output is a complete model in `outputs/sft-merged/`, including processor files and merge provenance. Evaluate this actual merged model; do not substitute adapter-mode predictions for it.

## 8. Compare Base and merged SFT

First use `data/evaluation_demo.jsonl` for a 20-question check. For the full comparison, replace it with `data/evaluation_candidates.jsonl` in both inference commands and the evaluation command.

```bash
python -m docreranker.inference \
  --input data/evaluation_demo.jsonl --output outputs/base-predictions.jsonl \
  --model models/qwen2.5-vl-7b --device cuda:0 --dtype bfloat16 \
  --batch-size 1 --max-new-tokens 768 --max-pixels 401408
python -m docreranker.inference \
  --input data/evaluation_demo.jsonl --output outputs/sft-predictions.jsonl \
  --model outputs/sft-merged --device cuda:0 --dtype bfloat16 \
  --batch-size 1 --max-new-tokens 768 --max-pixels 401408
python -m docreranker.evaluation \
  --input data/evaluation_demo.jsonl --predictions outputs/sft-predictions.jsonl \
  --output outputs/sft-metrics.json
```

Evaluate Base similarly with `--predictions outputs/base-predictions.jsonl --output outputs/base-metrics.json`. The reports include retrieval-order baseline metrics, reranker metrics, their differences, and malformed-output fallback counts. An invalid generated order falls back to the original retrieval order. Recall divides by all gold pages, including those absent from the candidates. With the same five candidates, reranking changes Recall@1 but cannot change Recall@5.

## 9. Prepare fresh GRPO data and train

GRPO samples several answers for the same question and rewards them relative to their group. Our reward is **strict format validity + normalized relevant-page gain discounted by `1/rank^3`**. Invalid permutations receive zero for both rewards. Group-relative advantages guide updates, clipping limits probability changes, and a KL penalty discourages excessive drift from the starting SFT policy. The student still sees questions and real images; labels are used by the reward function.

Use questions and documents **unused by SFT**, and preserve their original five retrieved candidates. No Gemini annotations are needed for this stage:

```bash
python scripts/prepare_grpo_data.py select \
  --queries data/splits/train_queries.jsonl --sft data/sft.jsonl \
  --evaluation-queries data/evaluation/queries.jsonl \
  --limit 64 --output data/grpo_queries.jsonl
python -m docreranker.retrieval search \
  --queries data/grpo_queries.jsonl --index data/index/train \
  --output data/grpo_candidates.jsonl --top-k 5 --device cuda
python scripts/prepare_grpo_data.py build \
  --input data/grpo_candidates.jsonl --output data/grpo.jsonl
```

The selection helper checks normalized question/document overlap. Conversion derives local `positive_ids` from dataset gold IDs, validates the real images, and preserves candidate order. Groups with no positive-negative ranking signal are skipped and reported in `data/grpo.skipped.jsonl`; no gold pages are inserted. Omit `--limit` to retrieve every eligible unused question when preparing a larger run.

Start with the supplied **one-GPU, 32-update practice configuration**:

```bash
python -m docreranker.training.grpo \
  --config configs/grpo-student.json --data data/grpo.jsonl \
  --model outputs/sft-merged --output outputs/grpo \
  --exclude-queries data/evaluation/queries.jsonl --dry-run
python -m docreranker.training.grpo \
  --config configs/grpo-student.json --data data/grpo.jsonl \
  --model outputs/sft-merged --output outputs/grpo \
  --exclude-queries data/evaluation/queries.jsonl
```

This configuration uses a fresh rank-64/alpha-128 LoRA adapter, four sampled completions per question, two reuse iterations, learning rate `1e-5`, clipping epsilon `0.2`, and KL coefficient `0.04`. Inspect `outputs/grpo/docreranker_training.json` and saved checkpoints. It is a short practice run, not the 768-update experiment.

The [recorded experiment configuration](experiment/grpo-config.json) uses **four GPUs**, a generation batch of 16 completions, optimizer batch of 8, and 768 updates. Its original server model path must be overridden. To use those settings with your own sufficiently large prepared GRPO pool:

```bash
torchrun --standalone --nproc_per_node=4 -m docreranker.training.grpo \
  --config experiment/grpo-config.json --data data/grpo.jsonl \
  --model outputs/sft-merged --output outputs/grpo-full \
  --exclude-queries data/evaluation/queries.jsonl
```

The recorded run used 768 fresh training questions; checkpoint 512 was selected using a validation rule declared before testing. A new dataset selection or small practice run is a new experiment. Before examining test results, declare the checkpoints you will compare and select the highest validation Macro Recall@1, breaking ties in favor of the earlier checkpoint. Build the validation candidates once, without gold insertion:

```bash
python -m docreranker.retrieval index \
  --pages data/splits/validation_pages.jsonl --output data/index/validation \
  --model models/colqwen2 --batch-size 4 --device cuda
python -m docreranker.retrieval search \
  --queries data/splits/validation_queries.jsonl --index data/index/validation \
  --output data/validation_candidates.jsonl --top-k 5 --device cuda
```

For each declared checkpoint, run the inference and evaluation commands below with `--input data/validation_candidates.jsonl`, its adapter path, and a distinct prediction/report output filename. Compare the reports' `reranker["macro_recall@1"]` values. Freeze the selected checkpoint before running its test evaluation.

To evaluate your practice GRPO model, use the merged SFT model **plus the GRPO adapter**:

```bash
python -m docreranker.inference \
  --input data/evaluation_demo.jsonl --output outputs/grpo-predictions.jsonl \
  --model outputs/sft-merged --adapter outputs/grpo \
  --device cuda:0 --dtype bfloat16 --batch-size 1 \
  --max-new-tokens 768 --max-pixels 401408
python -m docreranker.evaluation \
  --input data/evaluation_demo.jsonl --predictions outputs/grpo-predictions.jsonl \
  --output outputs/grpo-metrics.json
```

For `outputs/grpo-full/`, pass the selected adapter directory, for example `--adapter outputs/grpo-full/checkpoint-512`, instead. All compared models must use the same original evaluation candidate file and inference settings.

## 10. Inspect page images and predictions

```bash
streamlit run src/docreranker/demo.py -- \
  --input data/evaluation_demo.jsonl --predictions outputs/sft-predictions.jsonl
```

Open the local address printed by Streamlit, usually `http://localhost:8501`. Replay mode displays the saved model output and before/after page order without GPU inference. Live mode loads the chosen local model; set the merged model and optional GRPO adapter paths in the sidebar. The separate [interactive walkthrough](https://docreranker-classroom.litong1812.chatgpt.site) explains RAG, data, MaxSim, SFT/LoRA, and GRPO without installing Python.

## Recorded full-test results

These are the completed project's results on the **same 1,658-question test set** with fixed retrieved candidates. They are reference results, not expected outputs of the small practice commands.

| Reranker | Macro Recall@1 |
| --- | ---: |
| Base Qwen2.5-VL-7B | 61.09% |
| SFT900, merged | 63.89% |
| SFT + GRPO512 | 65.72% |

Saved metrics and the project report are in [metrics-current.json](experiment/metrics-current.json) and [current-test-report.md.txt](experiment/results.md). The repository includes source, small saved page/output examples, and run settings; it does not distribute the full processed training set, paid annotation cache, or trained checkpoints. Generating your own annotations and weights is part of the workflow above.

## Find the implementation

| Step | Code |
| --- | --- |
| Download, convert, and isolate data | `src/docreranker/data.py`, `scripts/prepare_training_data.py` |
| Page encoding, MaxSim, and candidate selection | `src/docreranker/retrieval.py` |
| Per-page Gemini notes and text refinement | `src/docreranker/annotation.py` |
| Prompt and complete-permutation format | `src/docreranker/prompts.py` |
| Visual SFT and LoRA merge | `src/docreranker/training/sft.py`, `merge.py` |
| Fresh GRPO data and training | `scripts/prepare_grpo_data.py`, `src/docreranker/training/grpo.py` |
| Deterministic GRPO rewards | `src/docreranker/training/rewards.py` |
| Generation, parsing, and fallback | `src/docreranker/inference.py` |
| Gold-based ranking metrics | `src/docreranker/evaluation.py` |

## Troubleshooting

- **`ModuleNotFoundError: docreranker`:** activate `.venv`, return to the repository root, and run `python -m pip install -e '.[data,train,demo,dev]'`.
- **`hf: command not found`:** install the `data` extra in the active environment. You can also run `python -m pip install 'huggingface-hub>=0.34,<1'`.
- **CUDA unavailable:** check that you are on an allocated GPU machine and that `nvidia-smi` works. A login node or CPU-only machine cannot run the GPU steps.
- **CUDA out of memory:** use inference/index batch size 1; lower `processor.max_pixels` for a new training experiment while keeping it at least `min_pixels`; enable gradient checkpointing. Smaller image budgets change the experiment. Do not truncate multimodal prompts or enable SFT packing.
- **Image path does not exist:** rerun conversion in the current project location. JSONL metadata contains absolute paths.
- **Annotation 401 or insufficient credits:** verify the key in `.env`, pass `--env-file .env --execute`, and check OpenRouter credits. Default annotation mode only previews costs.
- **Not enough annotation examples or unused GRPO documents:** prepare a larger training pool or lower the requested limit. Never fill the gap with evaluation questions.
- **Output directory is not empty:** choose a new output directory, or resume an actual Trainer checkpoint with `--resume outputs/sft/checkpoint-N`. GRPO enlarged-generation runs require checkpoints aligned with a complete generation cycle.
- **Index reports changed images or model files:** finish all writes/downloads, then rerun indexing before search.
- **GRPO rejects the model:** pass `outputs/sft-merged/`, created by a completed SFT run and merge. An SFT adapter directory or an untrained local base model is insufficient.

To run the CPU checks:

```bash
python -m pytest
```

Keep dataset and model licenses/citations from their official resource pages when reusing or sharing your work.
