# 检索教学事实核查 / Retrieval teaching fact check

审查日期：2026-09-14。仅检查保存的项目代码、数据元信息及官方技术来源；没有运行模型、修改训练数据或改动 GRPO。以下文件路径均相对 `Active/class/`。本文件区分本次实现、一般原理和没有证据支持的推断。

## 1. 两个模型各做什么 / The two models

**ColQwen2 是本项目使用的视觉页面检索器。** 当前 checkpoint 是 `vidore/colqwen2-v1.0-hf`，本地目录为 `models/colqwen2`。它基于 Qwen2-VL-2B-Instruct，为文本与页面图像输出多向量表示；当前配置中每个向量有 128 个数。学生可以先把 embedding 理解为“模型将内容转成可比较的一串数”，再理解“一个问题和一页都保留多串数，用于局部匹配”。它直接处理页面图像，无需本项目先进行 OCR。[官方模型卡](https://huggingface.co/vidore/colqwen2-v1.0-hf)、[官方配置](https://huggingface.co/vidore/colqwen2-v1.0-hf/blob/main/config.json)、[官方说明](https://huggingface.co/docs/transformers/model_doc/colqwen2)。

**Qwen2.5-VL-7B 是另一个用于重排的生成模型。** 它接收问题与候选页面，输出简短证据说明和页面编号排列。本项目的 SFT/LoRA 是让这个重排模型学习该任务；不是让 ColQwen2 重新学习 embedding。检索器分数也不是学生要复制的答案。

English: ColQwen2 retrieves candidate page images with 128-dimensional token vectors. Qwen2.5-VL-7B reads those candidate images and generates a ranking. They are separate models with separate roles.

本地依据：`models/colqwen2/config.json:5`（128维）、`:12`（Qwen2-VL-2B-Instruct）；`src/docreranker/retrieval.py:14`（checkpoint）、`:34`（encoder）。

## 2. 分数怎样算 / How the score is computed

设问题向量为 `q₁…qₘ`，某一页面向量为 `d₁…dₙ`：

```text
score(query, page) = Σᵢ maxⱼ (qᵢ · dⱼ)
```

教学顺序：先对一个问题向量与每个页面向量求点积；选出该行最大值；对其余问题向量重复；把每行最大值相加，得到这一整页的分数。再对其他页面重复，分数高的页排前面。这个机制叫 MaxSim，也叫后期交互：问题与页面可以各自提前编码，比较时才让两组向量相遇。

当前 Transformers 实现先把每个 token 向量做 L2 归一化，然后按 attention mask 将补齐位置置零。项目 encoder 又用该 mask 删除补齐位置，只保存有效向量。因而有效向量的点积近似余弦相似度；浮点存储会带来少量数值误差。`maxsim()` 本身不会再次归一化，它使用模型已经归一化的向量。

重要细节：问题编码还含 `Query: ` 前缀与增强 token；页面编码还含图像模板与特殊 token。因此“每个词”和“每个小图块”只是入门近似，真实序列并不只包含自然语言词或纯图像区域。增强 token 即使采用与 padding 相同的 token ID，只要其 `attention_mask=1` 就会被保留。

真实例子的 `18.639074…` 是多个匹配值之和，**不是 18.6% 概率，也不是正确标签**。不同长度问题的累计分也不宜直接当作统一置信度。排序关注同一问题下各页面的相对分数。

本地依据：`src/docreranker/retrieval.py:21`（MaxSim）、`:45`（mask去padding）；`.venv/lib/python3.11/site-packages/transformers/models/colqwen2/modeling_colqwen2.py:197`（投影）、`:201`（L2归一化）；同目录 `processing_colqwen2.py:85`（页面模板）、`:89`（问题前缀）、`:209`（增强token）、`:330`（官方评分函数）。[对应版本官方模型源码](https://github.com/huggingface/transformers/blob/v4.57.3/src/transformers/models/colqwen2/modeling_colqwen2.py)、[对应版本官方 processor 源码](https://github.com/huggingface/transformers/blob/v4.57.3/src/transformers/models/colqwen2/processing_colqwen2.py)。

English: Normalize each token vector, compare every query vector with all page vectors, retain one maximum per query vector, and sum those maxima. Padding is removed by the project encoder. This is a similarity score, not a probability or a relevance label.

### 可手算的人工例子 / A fully artificial example

为便于手算，用三个三维单位向量作为问题：`Q = I₃`。页面三个单位向量为 `d₁=(1,0,0)`、`d₂=(0,0.8,0.6)`、`d₃=(0.6,0,0.8)`。所有向量长度均为1。

| 问题向量 | 页面 d₁ | 页面 d₂ | 页面 d₃ | 行最大值 |
| --- | ---: | ---: | ---: | ---: |
| q₁ | 1.0 | 0.0 | 0.6 | 1.0 |
| q₂ | 0.0 | 0.8 | 0.0 | 0.8 |
| q₃ | 0.0 | 0.6 | 0.8 | 0.8 |

页面总分为 `1.0 + 0.8 + 0.8 = 2.6`。这是符合归一化条件的人工教学数字；真实模型使用128维，且真实 REITs 分数不是由此表算出。

## 3. 建索引与查询 / Indexing and querying

先给可用页面图片逐页编码，保存 `.npy` 向量与页面清单；以后每来一个新问题，只编码问题一次，再与检索范围内每页已保存的向量计算 MaxSim。当前代码逐页精确打分，不调用近似最近邻向量数据库。图片、模型或 processor 改变时，原索引可能需要重建。

本地依据：`src/docreranker/retrieval.py:125`（建索引）、`:223`（确定范围）、`:231`（编码问题并逐页计算分数）。

## 4. 为什么是五个 / Why five candidates

**K=5 是本次实验选择的候选预算，是可修改的超参数。** 检索 CLI 默认5；本次包装脚本也显式传5。五页让重排模型有几个可比较的竞争页面，同时限制多图输入和生成的计算成本。提高 K 通常给检索阶段更多保留证据页的机会，也给后续模型带来更多图像与更长输入；是否让最终成绩更好，需要在验证集上比较不同 K。

没有找到本项目的 K 消融，不能称“五个最优”“模型只能处理五个”或“五来自数据集标签规定”。固定五个候选只重排时，Recall@5 不变；若相关页面没有进入候选，重排也无法把它补回来。Recall@1、Recall@3、MRR、nDCG 则可以因顺序改变。

本地依据：`src/docreranker/retrieval.py:262`（默认5）、`scripts/retrieve_candidates.py:72`（显式5）。旧参考 `reranker/src/sft/prepare_data_with_score.py:34` 设置 `n_candidates=10`，`:36` 设置 `n_negatives=4`，因此常见单正例会得到1正+4负的五页，但不能声称旧代码固定每题五页。旧评估的最多50页又是另一设置；见 `docs/reference-comparison.md:18`。

English: Five is the current candidate budget, not a dataset requirement or a proven optimum. More candidates trade additional retrieval opportunities for more reranking input and compute. A fixed candidate set limits what reranking can recover.

## 5. 数据集与标签 / Dataset and labels

MMDocIR 用于研究多模态长文档检索。问题所需证据可能是文字、表格、图像或多个页面；官方同时提供页面与更细布局层级的检索任务。本项目使用页面层级：输入问题与页面图片，判断哪一页应更靠前。[MMDocIR 官方论文项目页](https://mmdocrag.github.io/MMDocIR/)。

当前下载的训练文件来自7个来源：ArxivQA、DUDE、MP-DocVQA、SciQAG、SlideVQA、TAT-DQA、Wiki-ss。原始 JSONL 已有 `query`、`positive_passages`、`negative_passages`，图片和源页号位于相应 Parquet 中。当前文件名单可见[官方训练 Parquet 目录](https://huggingface.co/datasets/MMDocIR/MMDocIR_Train_Dataset/tree/main/parquet)；标签 schema 可见[官方训练数据页](https://huggingface.co/datasets/MMDocIR/MMDocIR_Train_Dataset)。

正式 evaluation 是另一个发布集：官方数据卡列313份文档、1658个问题、20395张页面图像，标注中 `page_id` 给出证据页。项目页不同段落混用过1658/1685、73843/173843等数字及不同训练来源；因此不要把某个论文/官网版本的总体数量写成本次实际训练条数。本项目的训练、验证与测试条数应来自本次保存的清单。[官方 evaluation 数据卡与字段说明](https://huggingface.co/datasets/MMDocIR/MMDocIR_Evaluation_Dataset/blob/main/README.md)。

**标签在使用 ColQwen2 之前已经存在。** 转换代码把 `positive_passages` 映射到 `positive_page_ids`；检索代码用页面是否属于这个集合来赋值 `relevant`。训练阶段保留全部已知正例，再从其余页面中选检索分数较高的困难负例；测试阶段只按模型分数取候选，禁止用标签补进漏掉的正例。

之后，标注程序先按相关性把正例放前面，同类按检索分数建立确定顺序；打乱输入图像后重新映射1至K的局部编号。教师模型提供页面证据说明。完整排列是程序根据标签和这套规则构造的；不是数据集原本给了唯一的五页语义顺序，也不是让 ColQwen2 的分数推翻原始相关性标签。

本地依据：`src/docreranker/data.py:24`（来源列表）、`:121`（映射标签）；`src/docreranker/retrieval.py:165`（gold）、`:169`（训练候选）、`:185`（relevant）；`src/docreranker/annotation.py:104`（目标排列与打乱）。

English: The dataset supplies relevance labels before retrieval. ColQwen2 supplies scores and candidate choices. The training program derives an ordered target from known relevance plus a tie-breaking rule, and the teacher writes evidence explanations. These are different sources of supervision.

## 6. REITs 真例子与七页范围 / The actual seven-page pool

`train:SlideVQA:11570` 问题为 `In what year did REITS start in the US?`。原始记录的正例是源页号4，原始示例负例是源页号1：`data/raw/train/annotations_top1_negative/SlideVQA_train.jsonl:11571`。

直接以 PyArrow 读取 `data/raw/train/parquet/SlideVQA_filter.parquet` 的 `file_name` 和 `page` 列，核实该文档在官方过滤版文件中提供7页：`[1,4,5,15,16,20,21]`。它们全部出现在 `data/splits/train_pages.jsonl:20092` 开始的7条记录中。因此课堂应说“在数据为该文档提供的全部可用页面中检索”，不能说“原PDF总共只有7页”，也不能把单条标注中的1正1负当作检索范围。

本次原始检索序是 `[4,5,1,21,20]`，分数约为 `[18.6391,15.7189,15.4208,13.3414,13.2123]`。该题 `gold_injected=false`，说明正例原本就在前五里。训练输入打乱成 `[1,5,4,20,21]` 后，正例成为局部编号3，目标排列为 `[3,2,1,5,4]`。它演示监督如何形成；并不证明该题经过 SFT 的测试效果提高。

English: The source release provides seven available pages for this document. This is not a claim that the original presentation has only seven slides. The positive page label predates retrieval, and the model already ranked it first in this training example.

下载版本：训练 `059e7a30e87429698eaead28c30b1613dcf915c8`；evaluation `bdcb36ecb3eee73667180ee3fb24fe433f6dd2a4`。版本来自 `data/raw/train/download_manifest.json` 与 `data/raw/evaluation/download_manifest.json`。
