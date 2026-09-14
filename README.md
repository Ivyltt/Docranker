# DocReranker · 文档检索与重排 / Document retrieval and reranking

课堂网页 / Classroom: https://docreranker-classroom.litong1812.chatgpt.site

项目资源 / Project resources: [模型与训练代码 / Model & training code](src/docreranker/) · [教学网页源码 / Classroom source](teaching-web/) · [本轮 GRPO 设置 / GRPO run settings](experiment/grpo-config.json)

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
| `training/grpo.py`, `training/rewards.py` | GRPO implementation and reward functions; selected checkpoint 512 |
| `inference.py`, `evaluation.py` | Generate rankings and measure retrieval/reranking results |

`experiment/grpo-config.json` 保存本轮实际设置；`configs/` 是通用课堂默认设置。复现本轮需准备相同格式的训练图片与标签，并将模型路径指向自己的合并 SFT 权重。

`experiment/grpo-config.json` preserves the actual run settings; `configs/` contains general classroom defaults. Preparing the run requires training images and labels in the documented format and a model path pointing to your merged SFT weights.

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

网页源代码在 `teaching-web/`。公开课堂使用同一个固定链接，每分钟检查新版本并提示加载。GitHub 中的代码是最近一次推送的实现；GRPO 后续修改需重新推送，课堂展示 GRPO 原理、训练过程和同一测试集结果。

Website source lives in `teaching-web/`. The public classroom keeps a stable URL and checks for a new version every minute. GitHub contains the latest pushed implementation; subsequent GRPO changes must be pushed again. The classroom teaches GRPO principles, the completed run and same-test-set results.

## Same-test-set comparison / 同一测试集对照

固定 1,658 题、相同候选图片和评估规则：Base **61.09%** → SFT900 merged **63.89%** → SFT + GRPO512 **65.72%**（Macro Recall@1）。SFT 与 GRPO 的格式回退均为零，GRPO 相对合并 SFT 增加 **1.83 个百分点**。GRPO 的第 512 步在验证阶段按预定规则选定。

On the same 1,658 questions and candidate images, Macro Recall@1 is **61.09%** for Base, **63.89%** for SFT900 merged and **65.72%** for SFT + GRPO512. Both trained models have zero format fallbacks. GRPO gains **1.83 percentage points** over merged SFT; validation selected step 512 using a predefined rule.
