# DocReranker · 本科生中英教学课堂 / Bilingual undergraduate classroom

解压并保留整个文件夹，用浏览器打开 `index.html`，默认从 RAG 入门开始。右上角 **English / 中文** 切换完整语言版本，保持当前章节、交互状态和已展开内容。两种语言使用相同的真实数据、页面和实验结果。

Unzip the complete folder and open `index.html` in a browser. The default starting point is RAG foundations. Use **English / 中文** to switch the lesson, interface, examples and glossary while preserving the current lesson, interaction state and expanded sections. Both languages use identical data and results.

## 课堂顺序 / Lesson sequence

1. **RAG 入门 / RAG foundations** — 问题 → 检索 → 重排 → 加入上下文 → 生成回答；先理解整个系统与本项目负责的部分。 Follow the complete system and locate the component this project trains.
2. **原始数据 / The data** — MMDocIR、真实 REITs 问题、页面图像、原始相关性标签，以及训练/验证/测试的用途。 Meet the dataset, evidence pages, labels and three data splits.
3. **筛出候选页 / Retrieve candidates** — 四种文档编码路线的对比图、选择 ColQwen2 的理由、检索与重排两种基线、页面索引、MaxSim 手算与可调 K。 Compare encoding methods, understand the baseline choice, then work through indexing, scoring and the candidate budget.
4. **构造监督答案 / Build the target** — 正例与困难负例、Gemini 逐页笔记与文本精炼的目的和分工、精炼前后例子、随机打乱与完整目标排列。 Follow per-page notes, refinement and code assembly, including what each stage receives and produces.
5. **用 SFT 学会重排 / Learn with SFT** — 训练输入与目标、token 损失、反向传播、LoRA 与 rank，再到加载适配器进行推理。 Understand what is learned, which parameters change and how the trained model is used.
6. **比较训练前后 / Compare the results** — 纯检索、未做本任务 SFT 的 Base 与 SFT；阅读指标、真实改善和退步案例。 Compare the same candidate sets and inspect both aggregate results and saved outputs.
7. **GRPO 基础 / GRPO basics** — 同题多次尝试、格式与排序奖励、组内相对优势及更新约束。 Learn the mechanism with artificial reward examples; ongoing experimental settings are separate.
8. **代码与实践 / Code & practice** — 按步骤跟做、查看输入输出、复制命令、打开源码和项目报告。 Follow the project steps with commands, source files and project reports.

概念章节按“先理解 → 看例子 → 动手算或操作 → 检查理解”展开，并提供带反馈的小测。基础概念、关键公式和教学例子直接可见；源码、数据准备、工程细节和详细核查材料按需展开。原始代码、模型输出和下载报告保留原文；英文界面标出中文原始资料。

The concept lessons move from explanation to example, interaction and a comprehension check with feedback. Core concepts, essential formulas and teaching examples remain visible. Source code, data preparation, engineering details and extended reviews are expandable. Original code, model outputs and reports retain their source language; Chinese source documents are labeled in the English interface.

制作监督与学生训练要分开看：**Gemini 输入问题、单张真实页面图片及正负标签，生成逐页分析，再做纯文字精炼；Qwen 学生 SFT 仍输入问题与全部真实候选图片，以精炼证据说明和完整排列作为监督目标。**图中将图片输入与文本目标分别连接到学生训练。

Separate supervision preparation from student training: **Gemini reads the question, one real page image and its relevance label, writes per-page analysis, then refines the text. Qwen SFT still consumes the question and all real candidate images, learning the refined evidence-and-order target.** The diagram separately connects image inputs and textual targets to student training.

## 教学互动与关键区别 / Interactions and essential distinctions

- **四路线图：**用同一张页面对比“文字 + BM25”“文字 + 单向量”“图像 + 单向量”“ColQwen2 图像 + 多向量”，将输入模态与向量数量分开讲。示意图可放大，不能当作实际热图或四组实测成绩。 **Four-route diagram:** compare input, representation and matching rule on the same page. The diagram illustrates architectures, not measured attention or benchmark results.
- **为什么选这个基线：**ColQwen2 适合页面视觉内容、公开模型可固定版本、页面表示可缓存；固定候选便于比较重排。**本项目没有证明它优于所有其他检索方式。**ColQwen2 的“不重排基线”与原始 Qwen 的“未做本项目 SFT 的重排基线”回答不同问题。 **Baseline choice:** visual-page suitability, reproducibility and reusable page encodings motivate the choice; controlled superiority over other retrievers has not been established here. Retrieval order and Base Qwen reranking are separate baselines.
- **MaxSim 手算：**从点积矩阵开始，逐行取最大值，再求和。交互矩阵是人工教学数据；真实 ColQwen2 每个位置使用128维向量，分数不是概率。 **Worked MaxSim:** reveal row maxima and their sum; distinguish a synthetic example from actual model scores.
- **调整 K：**观察候选数量如何影响证据覆盖与重排输入量。K 是候选预算，**不等于 gold 页数**；本次设置为5，并未用 K 消融证明其最优。 **Candidate budget:** changing K illustrates a tradeoff, not a measured claim that five is optimal.
- **追踪标签：相关性 label 来自数据集**，ColQwen2 提供分数和候选，代码构造完整排列，Gemini 提供证据摘要。 **Trace supervision:** relevance labels, retrieval scores, target orders and teacher summaries have different sources.
- **为什么逐页笔记再精炼：**先逐页覆盖证据和易混淆负例，再删重复、保留编号并统一说明格式。对照逐页笔记与精炼后摘要，明确精炼只接收文字笔记和代码给出的目标排列，不重新读取原图。额外说明需要教师请求，也可能带入事实错误；目前没有消融实验隔离其收益。 **Why notes, then refinement:** inspect each page, then consolidate evidence into a concise indexed summary. Refinement sees text notes and a code-built order, without the original images. Additional annotation costs and possible factual errors remain; its isolated benefit has not been measured.
- **SFT 与 LoRA：**调整目标 token 概率观察损失，改变 rank 观察低秩矩阵规模和参数量。小算例用于理解机制，不会改变实际模型。 **SFT and LoRA:** explore loss and adapter dimensions without training a model.
- **GRPO 奖励：**比较同一题的候选输出、奖励和相对优势；格式有效与排名正确都不自动证明解释事实正确。 **GRPO rewards:** inspect the learning signal and its limitations using a worked example.
- **每章小测：**作答后获得解释，帮助检查是否理解本章的数据流与概念。 **Chapter quizzes:** answer and read feedback before moving on.

## 打开方式 / Open the classroom

直接打开 HTML 不需要网络、前端依赖、GPU 或 API。页面互动不启动训练、模型推理或教师请求；新训练和预测需要完整项目源码、数据、图片、模型和合适 GPU。本包只包含教学展示资产，外部论文与官方文档链接需要联网。

The HTML works offline with no frontend dependencies, GPU or API calls. Its interactions do not launch training, model inference or teacher requests. Actual training and new predictions require the full project, data, images, models and suitable GPU resources. External paper and documentation links need an internet connection.

远程 IDE 中也可以在本目录启动并转发端口 / For a remote IDE, run this in the website folder and forward port 8766:

```bash
python3 -m http.server 8766 --bind 127.0.0.1
```

打开 / Open `http://localhost:8766`。强制语言 / Explicit language: `?lang=zh` or `?lang=en`。

方向键切换章节，`/` 查询术语，`P` 切换投影模式；输入框和弹窗使用自己的按键操作。学习标记与语言偏好保存在当前浏览器。

Use arrow keys for lessons, `/` for the glossary, and `P` for presentation mode. These shortcuts do not interfere with inputs or dialogs. Progress and language preference are saved in the current browser.

## 事实来源 / Provenance

- REITs 原始记录：`assets/sources/reits-original.json`，来自 `SlideVQA_train.jsonl` 第 11571 行。相关数据页号 4 → 原检索候选 1 → 打乱后的训练候选 3。原检索已排对首位，本例展示数据流，不代表 SFT 改善。
- Original REITs annotation: dataset page ID 4 → retrieval candidate 1 → shuffled training candidate 3. Retrieval already ranked it first; this example illustrates the data flow, not an SFT improvement.
- 该文档的7页是官方过滤版数据提供的全部可用页 `[1,4,5,15,16,20,21]`，不代表原始幻灯片总共只有7页。
- Its seven available source pages are not a claim that the original slide deck has only seven pages.
- Base/SFT 使用已完成的 1,658 题固定测试快照。回退与完整 gold 分母保留；案例的原始输出和人工核查仍可展开。
- Base/SFT results use the saved complete 1,658-query test snapshot, retaining fallbacks and all labeled relevant pages. Original case outputs and human reviews remain available.
- GRPO 没有接入正在修改的新实验；本轮没有改动训练代码、数据或模型。
- The ongoing GRPO experiment is not imported. This revision does not change training code, datasets or models.



ColQwen2、MaxSim、K、数据标签及官方来源核查：[assets/retrieval-teaching-notes.md](assets/retrieval-teaching-notes.md)。文档编码路线、选型理由和官方参考：[assets/encoding-comparison-notes-v4.md](assets/encoding-comparison-notes-v4.md)。网页正文也提供相关官方链接。


The retrieval notes cover the model, score calculation, candidate budget and dataset labels. The encoding-comparison notes add the representation comparison, baseline rationale and primary references. The lessons also link to relevant official sources.

来源哈希在 / Source hashes: `assets/provenance.json`。网页包清单在 / Package manifest: `package-manifest.json`。

## 验收与打包 / Verification and packaging

使用 `checks/browser_check.py` 检查两种语言的八章、教学互动、英文完整性、编号映射、真实输出原文、语言切换保持状态、图片、指标、词典、源码、离线链接、手机与投影。本轮新增内容也需核查四路线图、两种基线、教师分工及精炼前后展示。开发环境需另装 Playwright；无需改动训练环境。以下为 v5 检查与打包命令，不表示本轮检查已经通过。

Use the browser check for both languages, all eight chapters, interactions, translation completeness, index mapping, exact saved outputs, state preservation, images, metrics, glossary, source links, offline mode, mobile and projection layouts. This revision also requires review of the encoding diagram, baseline distinctions, teacher roles and refinement comparison. Install Playwright in a separate development environment. These are v5 verification instructions, not a statement that this revision has passed.

```bash
python checks/browser_check.py --browser /path/to/chromium --output /tmp/docreranker-web-v5-check
python checks/package.py
```

检查结果以执行命令后的输出与 `checks/verification.json` 为准；核对记录时间和对应版本。`checks/package.py` 校验展示资产哈希并生成父目录的 `teaching-web.zip`。

Consult the command output and `checks/verification.json` after execution, checking their timestamp and revision. Packaging verifies copied-asset hashes and creates `teaching-web.zip` in the parent folder.

## 公开分享与更新 / Public sharing and updates

公开地址与 GitHub 项目链接在 `site-config.json` 配置。公开构建：

```bash
python3 checks/export_code.py
python3 checks/build_public.py
```

`dist/` 仅包含课堂实际需要的静态文件。更新部署后继续分享同一网址；脚本与样式使用内容哈希，已打开页面会提示加载更新。详细维护说明见 [PUBLISHING.md](PUBLISHING.md)。

The generated `dist/` directory contains classroom runtime assets only. Deploy updates to the same address. Content-hashed files avoid stale scripts; existing tabs offer a reload when a new version is available. See [PUBLISHING.md](PUBLISHING.md).
