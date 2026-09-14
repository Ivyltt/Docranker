# Document encoding comparison: verified teaching notes (v4)

Checked 2026-09-14 against the local implementation and primary sources. These are teaching and source notes, not new experiments. No retriever or reranker was run.

## 中文：先分清两个设计问题

把页面变成模型可以比较的表示，叫**编码**。比较方法有两个独立选择：① 先提取文字，还是直接看整页图片？② 每个检索单元只保留一个向量，还是保留多个位置的向量？因此“视觉编码”和“多向量编码”不是同一个概念。

这里比较的是几种常见路线，不是所有模型的完整分类，也不是本项目已经做过的性能排行榜。文字路线可以从 PDF 直接抽取文字；扫描页通常需要 OCR。OCR 是把图中的文字识别出来。若把一页切成多个文本块，还需把块的命中结果映射回我们要排序的页面。

| 路线 | 页面怎么表示 | 问题和页面怎样比较 | 适合什么 / 代价是什么 |
|---|---|---|---|
| 文字提取 / OCR → BM25 | 词与出现次数，使用倒排索引；不要求神经网络向量 | 匹配查询中的词，考虑词频、词的稀有程度和文档长度 | 精确关键词强、实现直接；词面不同但意思相近时可能漏检。图表的布局和数值关系取决于前面的文字提取是否保留下来。 |
| 文字提取 / OCR → 单向量 dense | 每个文本块或页面一条稠密语义向量 | 查询向量与文本向量的点积或余弦相似度 | 可匹配部分同义表达，向量可缓存；文字提取遗漏的信息无法由下游直接看到，切块与单向量压缩也可能丢掉局部关系。 |
| 整页图片 → 单向量视觉编码 | 视觉编码器看页面，再汇聚成一个全局向量 | 一个问题向量与一个页面向量比较 | 能利用视觉信息、索引紧凑；整页多处细节需要装进同一个向量，细小数字或多个独立证据可能难以保留。这是架构上的可能限制，不是所有模型必然失败。 |
| 整页图片 → ColQwen2 多向量 | 保留多个上下文化位置表示，每条 128 维；问题也有多个向量 | 每个问题向量寻找最相似的页面向量，再将这些最大相似度相加（MaxSim） | 避免必须先把页面转成纯文字；比较保留到较细粒度。代价是每页存储多条向量、计算更多相似度；它也会读错细节或检索失败。 |
| 问题 + 少量页面 → 生成式 Qwen 重排 | 在同一上下文中读问题和全部候选图片 | 联合比较，再生成证据说明和完整页序 | 可针对这个问题比较候选之间的差异；每个新问题都要进行联合推理并逐 token 生成，因此本项目放在候选缩小之后。完整联合判断不能预先存成与问题无关的一个页面分数。 |

BM25 依据：[作者教材中的 BM25 定义](https://nlp.stanford.edu/IR-book/html/htmledition/okapi-bm25-a-non-binary-model-1.html)。单向量文本编码依据：[DPR 论文 §3.1](https://arxiv.org/html/2004.04906v3#S3.SS1)。视觉单向量与多向量路线依据：[ColPali 论文 §5.1–5.2](https://arxiv.org/html/2407.01449v6#S5.SS1)。以上“信息可能丢失”是针对表示方式的教学推论，不是本项目实测结论。

## 中文：为什么本项目选 ColQwen2 做基线

**基线是一条固定的比较起点。** 我们的问题是：给定同一批候选页面，训练后的重排器能否把相关页面排得更靠前？因此先固定 ColQwen2 的模型、图片处理与候选文件，再比较“直接使用其检索顺序”“原始 Qwen 重排”和“SFT 后的 Qwen 重排”。否则换了检索器、换了候选集，就难以判断增益来自哪里。

选择它有三个实际理由：① MMDocIR 的页面包含表格、图、版式，ColQwen2 可以直接读取页面图片；② 公开的 `vidore/colqwen2-v1.0-hf` checkpoint 可以固定版本，现有代码也能离线缓存每页向量，课堂流程容易复现；③ 多向量 MaxSim 提供一个明确可计算、无需生成长回答的检索顺序，适合在昂贵的生成式重排前缩小候选。**这些是任务与工程上的选择理由；本项目没有完成 BM25、文本 dense、视觉单向量与 ColQwen2 的同条件对比，不能据此宣称 ColQwen2 最优。**

本项目用的是基于 **Qwen2-VL-2B-Instruct** 的检索模型 ColQwen2；随后训练的是 **Qwen2.5-VL-7B-Instruct** 重排模型。两者不是同一个模型。我们没有通过 SFT 修改 ColQwen2，也不是把 ColQwen2 的检索分数当作相关性标签。相关页标签由数据集提供。

模型身份依据：[ColQwen2 官方模型卡](https://huggingface.co/vidore/colqwen2-v1.0-hf)；视觉多向量能力依据：[Transformers 官方文档](https://huggingface.co/docs/transformers/model_doc/colqwen2)。固定候选比较、标签与缓存行为以本项目代码为准。

## English: the two design choices

**Encoding** turns a page into a representation that a retrieval system can compare. Make two separate choices: (1) extract text, or read the page image directly; (2) keep one vector per retrieval unit, or keep several position vectors. Visual input does not automatically mean multi-vector retrieval.

These are common design patterns, not a complete taxonomy or a leaderboard measured in this project. Text may be extracted directly from a digital PDF; scanned pages usually need OCR, which recognizes text in an image. If a method retrieves text chunks, their results must be mapped back to the pages that our task ranks.

| Route | Stored representation | Comparison | Strength and tradeoff |
|---|---|---|---|
| Text extraction / OCR → BM25 | Terms and counts in an inverted index; neural vectors are not required | Query term matches weighted by frequency, rarity and document length | Useful for exact keywords; may miss differently worded concepts. Layout and chart relationships depend on what extraction preserves. |
| Text extraction / OCR → single-vector dense | One semantic vector per text chunk or page | Dot product or cosine similarity with one query vector | Can match some paraphrases; vectors can be cached. Missing extraction details are unavailable downstream, and chunking or pooling may lose local relationships. |
| Page image → single-vector visual encoding | A visual encoder pools page information into one vector | One query vector against one page vector | Uses visual input with a compact index. Small numbers or multiple distinct pieces of evidence may be difficult to preserve in one representation; this is a possible bottleneck, not guaranteed failure. |
| Page image → ColQwen2 multi-vector encoding | Multiple contextual position vectors, each 128-dimensional; the query also has multiple vectors | For each query vector, keep its highest dot product with any page vector; sum those maxima | Avoids requiring a separate text-only conversion and retains finer matching granularity. More vectors increase storage and scoring work; retrieval and fine-detail errors remain possible. |
| Question + a few pages → generative Qwen reranking | Joint processing of the question and all candidate images | Generates evidence notes and a complete page ordering | Can compare candidates for this particular question. Joint inference and token generation are repeated for each query, so we apply it after retrieval has reduced the candidate set. |

## English: why this baseline

A **baseline** is a fixed starting point for comparison. Our question is whether a trained reranker improves the order of the *same* candidate pages. We therefore freeze ColQwen2 and its candidate file, then compare its original order with Base Qwen and SFT Qwen reranking.

ColQwen2 fits our visual pages, has a public checkpoint whose version can be pinned, and lets us cache page representations offline. Its explicit similarity score makes a practical first stage before generative reranking. These are task and engineering reasons for choosing it. We have not run a controlled comparison against BM25, text dense retrieval and visual single-vector retrieval in this project, so we do not claim that it is the best retriever.

ColQwen2 uses a **Qwen2-VL-2B-Instruct** backbone. Our reranker is **Qwen2.5-VL-7B-Instruct**. SFT updates the reranker's adapters, not ColQwen2. The dataset supplies relevant-page labels; the retriever supplies scores and candidates.

## Recommended diagram

Use the **same small invented page** (title + 2×2 table + chart) at the left of four horizontal routes: text/BM25, text/single vector, visual/single vector, visual/multiple vectors. Label the figure as a *representation schematic*, not a measured saliency map. Draw a query separately. On the ColQwen2 route, draw several query vectors and page vectors, then a small pairwise matrix and “row maxima → sum”. Show two query vectors choosing the same page vector so students do not infer one-to-one assignment. Do not imply every stored vector corresponds exclusively to one printed word or image patch: vectors are contextualized sequence representations, and non-padding prompt positions can be retained too.

Color can show different positions; it must not claim actual model attention. A second small diagram can place the models on the project pipeline: `page cache + question → ColQwen2 (2B backbone) → top K → Qwen2.5-VL (7B) → notes + ordering`. Put “fixed” above ColQwen2 and “Base / SFT comparison” above the reranker. Place dataset labels below the pipeline with a separate arrow to training target construction / evaluation, not into test-time retrieval.

## Local implementation facts and caveats

- `src/docreranker/retrieval.py:14`: exact default checkpoint `vidore/colqwen2-v1.0-hf`.
- `retrieval.py:21–31`: `S(Q,D) = sum_i max_j(Q_i · D_j)`; float32 multiplication and aggregation. Scores are not probabilities and are most naturally compared for the same query.
- `retrieval.py:46–52`: model forward pass returns vectors; attention-mask padding is removed; stored arrays are float16. Model vectors are 128-dimensional. The on-disk vector payload is approximately `number_of_vectors × 128 × 2` bytes, excluding array and index metadata. Do not present one fixed per-page size: page sequence lengths vary.
- `retrieval.py:115–154`: cached `.npy` page representations keyed by image signature and model/configuration, with immutable model identity. This is an actual local file cache, not a demonstrated production ANN/vector-database deployment.
- `retrieval.py:219–231`: document scope is `(subset, doc_name)` supplied with the query, and the score is calculated against each page in that available scope. “Retrieve top K from a known document's available pages” is the evaluated setup. Do not imply the reported metrics demonstrate open-world full-corpus retrieval.
- `retrieval.py:262`: top K defaults to 5; `scope` defaults to `document`. More general `global` support exists but is a different protocol.
- `retrieval.py:186–187`: `relevant` is `page_id in gold`, independent of the numeric retriever score.
- `docs/evaluation.md:63–70`: reuse identical candidates, distinguish document scope from global scope, and never reduce the pool to only annotated positives and negatives.

## Additional primary sources

- [ColBERT paper](https://arxiv.org/abs/2004.12832): independently encode questions and documents, delay fine-grained interaction, cache document representations.
- [DPR full paper](https://arxiv.org/html/2004.04906v3): one vector for a text passage and one for a question, offline passage index, dot-product similarity; lexical and semantic retrieval have complementary failures.
- [ColPali paper](https://arxiv.org/html/2407.01449v6): visual single-vector and multi-vector comparisons in that paper's own benchmark. Do not import its timings, rankings or gains into our MMDocIR project.
- [BM25 textbook section](https://nlp.stanford.edu/IR-book/html/htmledition/okapi-bm25-a-non-binary-model-1.html): term frequency, inverse document frequency and length normalization.
