# DocReranker · 文档检索与重排 / Document retrieval and reranking

课堂网页 / Classroom: https://docreranker-classroom.litong1812.chatgpt.site

问题 → ColQwen2 召回真实页面 → Qwen 观察候选图片并重排。数据集提供正确页面标签；ColQwen2 提供检索分数，二者不同。训练候选可加入已知正例，正式测试只使用真实召回。

Question → ColQwen2 retrieves real pages → Qwen reads the candidate images and reranks them. Gold page labels come from the dataset, while retrieval scores come from ColQwen2. Training may insert a known positive page; evaluation uses actual retrieved candidates.

## Code map

| Module under `src/docreranker/` | Purpose |
| --- | --- |
| `data.py` | Read MMDocIR questions, pages and gold labels |
| `retrieval.py` | Encode page images and questions; compute late-interaction scores |
| `annotation.py` | Gemini reads each real page with its label, then refines the analyses using text |
| `prompts.py` | Number candidate slots and specify the output permutation |
| `training/data.py` | Preserve real candidate images in student training inputs |
| `training/sft.py` | Learn the evidence and ranking targets with LoRA |
| `training/merge.py` | Merge an SFT adapter into the base model |
| `training/grpo.py`, `training/rewards.py` | Current GRPO implementation and reward functions; revision in progress |
| `inference.py`, `evaluation.py` | Generate rankings and measure retrieval/reranking results |

学生 SFT 输入仍为问题和真实候选图片。正负标签只用于教师构造监督，不作为学生输入。教师的纯文字精炼不会把学生训练变成纯文字训练。

Student SFT inputs still contain the question and real candidate images. Relevance labels guide teacher supervision and are absent from student inputs. Text-only teacher refinement does not make student training text-only.

## Start with local checks

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
python scripts/teaching_example.py --help
```

数据、图片和模型权重需另外准备；网页的代码实践章节按顺序解释数据、检索、标注、SFT 和评估命令。模型步骤需要相应的 `data`、`model` 或 `train` 可选依赖及 GPU。教师 API 使用环境变量 `OPENROUTER_API_KEY`，请勿写入源码。

Datasets, page images and model weights are obtained separately. The classroom's code practice chapter explains the data, retrieval, annotation, SFT and evaluation commands in order. Model stages require the relevant `data`, `model` or `train` extras and a GPU. Teacher API calls read `OPENROUTER_API_KEY` from the environment.

## Classroom updates

网页源代码在 `teaching-web/`。公开课堂使用同一个固定链接，每分钟检查新版本并提示加载。GitHub 中的代码是最近一次推送的实现；GRPO 后续修改需重新推送，课堂目前只讲 GRPO 原理。

Website source lives in `teaching-web/`. The public classroom keeps a stable URL and checks for a new version every minute. GitHub contains the latest pushed implementation; subsequent GRPO changes must be pushed again. The classroom currently teaches GRPO principles.
