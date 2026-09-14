/* Compact bilingual lessons; shared commands keep both versions consistent. */
(() => {
  const code = value => '<div class="code-block"><pre><code>' + value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</code></pre></div>';
  const commands = {
  "environment": "cd Active/class\n.venv/bin/python --version\n.venv/bin/python -m docreranker.data --help\n.venv/bin/python -m docreranker.training.sft --help",
  "install": "python3 -m venv .venv-student\n.venv-student/bin/python -m pip install -e '.[train,data]'",
  "dryrun": ".venv/bin/python -m docreranker.training.sft \\\n  --config configs/sft.json \\\n  --data data/sft-flash-lite.jsonl \\\n  --output outputs/student-sft \\\n  --exclude-queries data/evaluation/queries.jsonl --dry-run",
  "train": "CUDA_VISIBLE_DEVICES=0 .venv/bin/python -m docreranker.training.sft \\\n  --config configs/sft.json \\\n  --data data/sft-flash-lite.jsonl \\\n  --model models/qwen2.5-vl-7b \\\n  --output outputs/student-sft \\\n  --exclude-queries data/evaluation/queries.jsonl",
  "merge": ".venv/bin/python -m docreranker.training.merge \\\n  --adapter outputs/student-sft \\\n  --output outputs/student-sft-merged --dtype bfloat16",
  "predict": ".venv/bin/python -m docreranker.inference \\\n  --input data/evaluation_candidates.jsonl \\\n  --output outputs/student-base-predictions.jsonl \\\n  --model models/qwen2.5-vl-7b --device cuda:0 --dtype bfloat16 \\\n  --max-new-tokens 256 --min-pixels 200704 --max-pixels 401408\n\n.venv/bin/python -m docreranker.inference \\\n  --input data/evaluation_candidates.jsonl \\\n  --output outputs/student-sft-predictions.jsonl \\\n  --model outputs/student-sft-merged --device cuda:0 \\\n  --processor outputs/student-sft-merged --dtype bfloat16 \\\n  --max-new-tokens 256 --min-pixels 200704 --max-pixels 401408",
  "evaluate": ".venv/bin/python -m docreranker.evaluation \\\n  --input data/evaluation_candidates.jsonl \\\n  --predictions outputs/student-base-predictions.jsonl \\\n  --output outputs/student-base-metrics.json\n\n.venv/bin/python -m docreranker.evaluation \\\n  --input data/evaluation_candidates.jsonl \\\n  --predictions outputs/student-sft-predictions.jsonl \\\n  --output outputs/student-sft-metrics.json",
  "existing": ".venv/bin/python -m docreranker.evaluation \\\n  --input data/evaluation_candidates.jsonl \\\n  --predictions outputs/flash-lite-reference4/evaluation/base-predictions.jsonl \\\n  --output outputs/student-existing-base-metrics.json\n\n.venv/bin/python -m docreranker.evaluation \\\n  --input data/evaluation_candidates.jsonl \\\n  --predictions outputs/flash-lite-reference4/evaluation/sft-predictions.jsonl \\\n  --output outputs/student-existing-sft-metrics.json",
  "annotation": ".venv/bin/python -m docreranker.annotation \\\n  --input data/train_candidates.jsonl \\\n  --output outputs/student-annotation-preview.jsonl \\\n  --model google/gemini-2.5-flash-lite \\\n  --exclude-queries data/annotation-excluded-query-ids.jsonl \\\n  --limit 4",
  "paidAnnotation": ".venv/bin/python -m docreranker.annotation \\\n  --input outputs/student-train-candidates.jsonl \\\n  --output outputs/student-pilot.jsonl \\\n  --cache-dir outputs/student-annotation-cache \\\n  --model google/gemini-2.5-flash-lite --env-file .env \\\n  --workers 1 --limit 4 --max-cost-usd 1 --execute",
  "prepare": ".venv/bin/python scripts/prepare_training_data.py --limit-queries 10000 --image-workers 4\n\n.venv/bin/python -m docreranker.retrieval index \\\n  --pages data/splits/train_pages.jsonl \\\n  --output outputs/student-index-train \\\n  --model vidore/colqwen2-v1.0-hf\n.venv/bin/python -m docreranker.retrieval search \\\n  --queries data/splits/train_queries.jsonl \\\n  --index outputs/student-index-train \\\n  --output outputs/student-train-candidates.jsonl --top-k 5 --training\n\n.venv/bin/python -m docreranker.retrieval index \\\n  --pages data/evaluation/pages.jsonl --output outputs/student-index-test --model vidore/colqwen2-v1.0-hf\n.venv/bin/python -m docreranker.retrieval search \\\n  --queries data/evaluation/queries.jsonl --index outputs/student-index-test \\\n  --output outputs/student-test-candidates.jsonl --top-k 5",
  "read": ".venv/bin/python - <<'PY'\nimport json\nfrom pathlib import Path\n\ndef find_example(path):\n    with Path(path).open(encoding=\"utf-8\") as handle:\n        for line in handle:\n            row = json.loads(line)\n            if row[\"query_id\"] == \"train:SlideVQA:11570\":\n                return row\n    raise ValueError(\"REITs example not found\")\n\nsource = find_example(\"data/train/queries.jsonl\")\nsample = find_example(\"data/sft-flash-lite.jsonl\")\nprint(\"Question:\", source[\"query\"])\nprint(\"Dataset gold page IDs:\", source[\"positive_page_ids\"])\nprint(\"Positive input positions:\", sample[\"positive_ids\"])\nfor index, (page, image) in enumerate(zip(sample[\"page_ids\"], sample[\"images\"]), 1):\n    print(index, page, \"Image exists:\", Path(image).is_file())\nprint(\"SFT target:\", sample[\"messages\"][-1][\"content\"])\nPY"
};
  window.CLASS_LESSONS = {
    zh: {
      sft: String.raw`
<p class="lead"><strong>SFT（Supervised Fine-Tuning，监督微调）用示范答案继续训练模型。</strong>我们从已经会看图、读文字、遵循指令的 Qwen2.5-VL-7B-Instruct 出发，教它根据页面证据输出完整排列。</p>
<p class="callout" id="sft-visual-input"><strong>学生的训练输入仍是问题 + 真实候选图片。</strong>上一节的教师“看图分析 → 文字精炼”制作的是助手监督答案；本节把它作为 completion，同时通过 images 字段加载真实图像。这里的 SFT 是多模态训练。</p>
<h3>1 · 模型学到的是什么？</h3>
<div class="grid-two">
<div class="card"><h4>参数与 token</h4><p><strong>参数</strong>是模型内部参与计算的数值，训练改变这些数值，进而改变输出概率。<strong>token</strong>是文本切分后的单位，可能是词、词的一部分或标点；一个 token 不一定等于一个字。</p></div>
<div class="card"><h4>prompt 与 completion</h4><p><strong>prompt</strong>是任务要求、问题和候选图片；<strong>completion</strong>是希望模型生成的助手答案。本例监督证据文字、格式和完整排列，不只监督排第一的编号。</p></div>
</div>
<p>不做本项目 SFT，原始指令模型也能接收同样的 prompt 并尝试排序，这就是 <strong>Base</strong>。SFT 让已有能力适应我们的任务；是否改善，要在新题上和 Base 比较。</p>
<h3>2 · 把上一节的样本分成输入与目标</h3>
<p>沿用 REITs 题，下面缩写证据文字，保留真实目标排列：</p>
<div class="code-block"><pre><code>prompt: 问题 "In what year did REITS start in the US?"
        + 候选图片 1、2、3、4、5 + 输出格式要求
completion: &lt;think&gt;第 3 张含美国 REITs 于 1960 年开始的证据；
             其余页未给出这一年份……&lt;/think&gt;
            &lt;answer&gt;[3, 2, 1, 5, 4]&lt;/answer&gt;
positive_ids: [3]  # 校验/评分元数据，不写入模型 prompt</code></pre></div>
<p><code>positive_ids</code> 是数据集正确页在当前候选中的位置。框架另有名为 <code>labels</code> 的<strong>目标 token 编号</strong>，用于算损失；这两种“标签”不是同一个数组。给新题排序时，模型只接收 prompt 和图片。</p>
<h3>3 · 一条示范怎样改变参数？</h3>
<ol>
<li><strong>Processor 编码：</strong>将文本转成 token 编号、图片转成视觉输入，保留图片与候选编号的对应。</li>
<li><strong>Forward 前向计算：</strong>在各位置预测下一个 token。训练使用示范中此前的正确文字作前缀，称为 <em>teacher forcing</em>；因果掩码阻止每个位置看见后面的答案。</li>
<li><strong>计算交叉熵（CE）：</strong>比较预测概率与正确 token，只对最后一条 assistant 的证据、格式和完整排列算损失。prompt、图像标记和填充位置不作为目标，但仍作为上下文参与计算。</li>
<li><strong>Backward 反向传播：</strong>计算梯度，即各个可训练参数微小变化会怎样影响损失，按需累积多题梯度。</li>
<li><strong>Optimizer 更新：</strong>AdamW 根据梯度、历史统计和学习率调整 LoRA 参数。清空梯度，继续下一批，重复后保存参数。</li>
</ol>
<div class="formula">单个目标 token 的 loss = −ln p<br>p 是模型给“正确下一个 token”的概率</div>
<p>人工算例：p 从 0.1 增至 0.8，损失从约 2.303 降至 0.223。训练汇总受监督 token 的损失；<strong>loss 下降表示更像示范，不等于测试集排序一定更好。</strong>例如证据用词更像教师，也能降低 loss。下方可拖动概率亲自计算。</p>
<p>训练可以并行计算各位置的预测；推理时没有正确前缀，模型必须接着自己生成的 token 继续写。这也是训练表现与实际排序可能不同的原因。</p>
<h3>4 · LoRA：用较少参数完成微调</h3>
<p><strong>LoRA（Low-Rank Adaptation，低秩适配）</strong>冻结原有大矩阵 W，学习两个小矩阵 A、B，以它们的乘积作为改变量。需要训练和保存的参数减少，基础模型仍需加载与计算。<a href="https://arxiv.org/abs/2106.09685" target="_blank" rel="noreferrer">LoRA 原论文</a></p>
<div class="formula">W<sub>有效</sub> = W + (α/r)BA<br>W: d<sub>out</sub> × d<sub>in</sub>；A: r × d<sub>in</sub>；B: d<sub>out</sub> × r</div>
<p><code>d_in</code> 与 <code>d_out</code> 是输入、输出维度；<code>r</code> 是控制改变量容量的小维度；<code>α/r</code> 调整改变量尺度。先经 A 再经 B，结果形状恰好与 W 相同。</p>
<div class="card"><h4>人工手算：一个 1,000 × 1,000 矩阵</h4><p>全量训练需要更新 1,000,000 个数。取 r=8，A 与 B 合计 <strong>8 × 1,000 + 1,000 × 8 = 16,000</strong>，为前者的 1.6%。这是教学矩阵，不是 Qwen 某层的真实尺寸；下方可以改变 r 比较容量。</p></div>
<p>本次记录使用 <strong>r=8、α=32，共 20,185,088 个可训练参数</strong>。LoRA 加在 Qwen 语言模块选定的线性层，基础权重及视觉模块被冻结。<strong>冻结只是“不更新参数”，模型仍会看图。</strong>这里改变重排模型，ColQwen2 保持原权重；LoRA 是训练方法，RAG 是检索后生成的工作流程。</p>
<h3>5 · 7,200 条样本为什么只更新 900 次？</h3>
<p><strong>batch</strong>是一次处理的样本数；<strong>梯度累积</strong>是处理几小批后合并更新一次，减少每批所需显存；<strong>epoch</strong>是把所选训练集遍历一轮。</p>
<table><thead><tr><th>配置</th><th>已完成的四卡训练</th><th>课堂单卡默认</th></tr></thead><tbody>
<tr><td>每卡 batch × GPU 数 × 累积轮数</td><td>1 × 4 × 2</td><td>1 × 1 × 8</td></tr>
<tr><td>有效 batch（每次更新的题数）</td><td>8</td><td>8</td></tr>
<tr><td>7,200 条 × 1 epoch ÷ 8</td><td>900 次更新</td><td>预期 900 次更新</td></tr>
</tbody></table>
<p>四卡各处理一题，累积两轮后合并梯度；单卡连续处理八题再更新。改变卡数时必须一起检查累积设置。</p>
<h3>6 · 训练后，怎样拿来排序？</h3>
<p><strong>adapter</strong>保存 LoRA 学到的增量，推理加载“对应基础模型 + adapter”。<strong>merge</strong>把增量合入基础权重，保存完整模型；合并不再训练。两种方式都接收同样的问题与候选图片，生成证据与排列。</p>
<div class="formula">训练一次 → 保存 adapter（或合并）→ 新问题经 ColQwen2 选候选 → Qwen 推理排序</div>
<p>每次使用无需再做 SFT。固定候选和推理设置，对比 Base 与 SFT 的独立测试指标；实践附录给出训练、加载 adapter、合并和评估命令。</p>
<details><summary>配置与源码：把教学步骤对应到实现</summary>
<p><code>training/data.py</code> 把 messages 拆为 prompt 与 completion。<code>completion_only_loss=true</code> 只监督 completion；此时 <code>assistant_only_loss=false</code> 不代表训练用户问题。<code>training/sft.py</code> 用 SFTTrainer 管理编码、损失、反向传播和更新；<code>training/common.py</code> 指定 LoRA 层及冻结视觉参数。</p>
<p>共同设置：学习率 10⁻⁴，AdamW，余弦调度，预热比例 0.03，BF16，SDPA，每图 200,704–401,408 像素。正式四卡关闭梯度重计算、每 50 步保存；单卡默认开启梯度重计算、每 epoch 保存。gradient checkpointing 用重算中间结果省显存，checkpoint 则保存训练进度。</p>
<p class="source-note"><a href="assets/sources/training-config-snapshot.json" target="_blank">正式配置快照</a> · <a href="assets/sources/src__docreranker__training__data.py.txt" target="_blank">数据与输入构造源码</a> · <a href="assets/sources/src__docreranker__training__sft.py.txt" target="_blank">SFT 源码</a>；课堂默认：<code>configs/sft.json</code>。</p>
</details>`,
      grpo: String.raw`
<p class="lead"><strong>GRPO（Group Relative Policy Optimization，组相对策略优化）从模型多次尝试的得分差异中学习。</strong>在这条教学流程里，从 SFT 后的 Qwen 出发，继续改进给候选页排序的行为。</p>
<div class="callout">先用人工小例子理解组内奖励与更新，再在本节后半部分查看我们已完成的 GRPO512 训练过程和同一测试集结果。</div>
<h3>1 · 从模仿一份示范，到比较多次尝试</h3>
<p><strong>策略（policy）</strong>是模型在已有上下文下对下一个 token 给出的概率分布；按概率采样，同一道题可以生成不同证据与排列。</p>
<ol>
<li><strong>固定问题和候选图片：</strong>加载 SFT 后的策略，给它与推理时一样的 prompt。</li>
<li><strong>生成一组输出：</strong>同题采样 G 份完整回答，每份有证据与排列。<strong>G 是尝试次数，K=5 是候选页数，两者独立。</strong></li>
<li><strong>程序评分：</strong>用数据集 gold 映射得到的 positive_ids 检查格式与正确页名次；标签不进入模型 prompt。</li>
<li><strong>比较并更新：</strong>将奖励与本组平均比较，计算优势。训练倾向提高正优势回答的生成概率、降低负优势回答的概率，再重新采样、继续学习。</li>
</ol>
<div class="formula">同题多次生成 → 每份一个奖励 → 组内相对优势 → 更新策略 → 再尝试</div>
<h3>2 · 排列怎样变成数字奖励？</h3>
<p>沿用课堂评分示意：总奖励 = 格式奖励 + 排序奖励。合法格式要求证据标签非空、答案包含 1–5 每个编号恰好一次；非法排列两项都为 0。</p>
<div class="formula">排序奖励 = 正确页在实际名次上的 Σ(1/名次³)<br>÷ 所有正确页排在最前时的 Σ(1/名次³)</div>
<p><code>Σ</code> 表示把每个正确页的得分相加。立方折扣让靠前名次更重要，分母把理想排列的排序奖励定为 1。“名次”是输出位置，不是候选编号。</p>
<div class="card"><h4>人工算例：gold = {2, 4}</h4><p><code>[2, 1, 4, 3, 5]</code> 把正确页放在第 1、第 3 名。实际得分为 <code>1 + 1/27</code>，理想为 <code>1 + 1/8</code>，排序奖励 <strong>224/243 ≈ 0.922</strong>。证据和格式合法再加 1，总奖励约 <strong>1.922</strong>。合法的 <code>[2, 4, 1, 3, 5]</code> 可得 2；<code>[2, 2, 4, 3, 5]</code> 因重复编号得 0。</p></div>
<h3>3 · 为什么减去同组平均分？</h3>
<p><strong>优势（advantage）</strong>衡量一次回答相对同题其他尝试的好坏。下面用组内总体标准差归一化：</p>
<div class="formula">A<sub>i</sub> = (R<sub>i</sub> − μ) / (σ + ε)<br>R<sub>i</sub>：第 i 份奖励；μ：组均值；σ：总体标准差；ε：避免除零的小正数</div>
<p>人工给一组奖励 <code>[1, 2]</code>：均值 1.5，总体标准差 0.5；忽略极小 ε，优势是 <strong>[−1, +1]</strong>。题目难度不同时，仍在各自题内比较。框架可采用不同标准差口径，下面互动固定用总体标准差，不代指训练实现。</p>
<p>GRPO 用组内分数提供比较基准，<strong>无需再训练 value/critic 模型来预测预期奖励</strong>；本例奖励又能由标签和程序直接算出。使用整份回答的结果奖励时，同一优势可用于该回答各生成 token 的更新。<a href="https://arxiv.org/html/2402.03300v3#S4.SS1" target="_blank" rel="noreferrer">GRPO 原论文</a></p>
<h3>4 · 怎样避免因一组样本改动太大？</h3>
<p><strong>旧策略</strong>是生成本组样本时的策略快照。对同一个 token、同一个前缀，计算：</p>
<div class="formula">ρ = 当前策略概率 / 旧策略概率</div>
<p>旧概率 0.20、当前 0.30，ρ=1.5。人工设裁剪范围 [0.8, 1.2]，正优势项不会因把比值推到 1.5 而继续获得同比例收益。<strong>clipping 限制目标函数中的更新激励，不是硬性保证所有概率变化不超过 20%。</strong></p>
<p><strong>参考策略</strong>是保持行为接近起点的基准，常选 SFT 模型，与采样用的旧策略作用不同。启用 <strong>KL 惩罚</strong>时，概率分布偏离参考策略越多，代价越大；系数 β 决定约束强度。裁剪管本轮更新激励，KL 管相对参考策略的偏离。<a href="https://huggingface.co/docs/trl/grpo_trainer#computing-the-loss" target="_blank" rel="noreferrer">TRL 原理说明</a></p>
<h3>5 · 分数能说明什么？</h3>
<table><thead><tr><th>比较</th><th>SFT</th><th>GRPO</th></tr></thead><tbody>
<tr><td>训练材料</td><td>问题、图片、固定示范答案</td><td>问题、图片、评分所需 gold；训练中生成多份答案</td></tr>
<tr><td>学习信号</td><td>目标 token 的交叉熵</td><td>奖励得到的相对优势</td></tr>
<tr><td>希望提高什么</td><td>示范答案的生成概率</td><td>组内较高奖励回答的生成概率</td></tr>
<tr><td>共同检验</td><td colspan="2">在同一测试集上计算排序指标，并检查页面证据是否真实</td></tr>
</tbody></table>
<p>整组同分，相对奖励优势均为 0；启用 KL 时仍可能存在其约束信号。组内最好也可能答错。本例奖励检查格式与排序，<strong>不会核实证据文字真假</strong>；训练奖励上升也不等于独立测试指标上升。下面改变两份输出，观察这些差别。</p>`,
      lab: String.raw`
<p class="lead">课堂先复用已经准备好的数据和标签。按需展开下面 6 步，完成读取样本、SFT 和对照评估。</p>
<p>命令均在 <code>Active/class</code> 执行，结果写入 <code>outputs/student-*</code>。第 1–3 步不训练模型；第 4–5 步的新训练与预测需要课程 GPU、模型和完整图片。离线网页包只用于展示。</p>
<details><summary>1 · 环境：找到项目与 Python</summary>
<p>从工作区根目录进入项目；已经在 <code>class</code> 时跳过第一行。</p>
${code(commands.environment)}
<p><strong>核对：</strong>出现 Python 版本和参数说明。课程已有 <code>.venv</code> 可直接使用；自己的机器用 Python 3.10+ 新建环境：</p>
${code(commands.install)}
<p>自建环境将后续 <code>.venv/bin/python</code> 换成 <code>.venv-student/bin/python</code>。<code>python -m</code> 运行项目模块；<code>--input</code> 等指定文件或设置。提示找不到 <code>docreranker</code> 时，检查解释器和安装目录。</p>
</details>
<details><summary>2 · 数据：读出课堂上的真实 REITs 样本</summary>
<p>下面按 query_id 找到同一道题，对照数据集的正确页 ID、训练候选编号和 SFT 目标。JSONL 是一行一个 JSON 对象；<code>python -</code> 运行两个 <code>PY</code> 之间的代码。</p>
${code(commands.read)}
<p><strong>核对：</strong>正确输入位置是 <code>[3]</code>，目标排列为 <code>[3, 2, 1, 5, 4]</code>。逐项将局部编号映射回真实 page_id，图片存在项应为 <code>True</code>。复制 JSONL 不会自动复制其绝对路径指向的图片。</p>
</details>
<details><summary>3 · 训练前：检查现有 7,200 条 SFT 数据</summary>
${code(commands.dryrun)}
<p><strong>核对：</strong><code>stage: sft</code>、<code>dry_run: true</code>、<code>source_examples: 7200</code>。程序检查图片、标签、排列和配置，不加载模型、不生成权重。此命令的排除项只比 query_id；课程此前还做过文档和问句隔离。检查通过不代表 GPU 显存一定够。</p>
</details>
<details><summary>4 · SFT：单卡训练，保存 adapter，再合并</summary>
<p>在已分配的单卡 GPU 环境运行。示例选择可用 GPU 0；若调度器已设置 <code>CUDA_VISIBLE_DEVICES</code>，保留调度器设置，去掉命令开头的覆盖项。每次新训练使用新的空输出目录。</p>
${code(commands.train)}
<p><strong>核对：</strong><code>outputs/student-sft</code> 中有 adapter、processor 和 <code>docreranker_training.json</code>，记录为 <code>dry_run: false</code> 且 <code>global_step &gt; 0</code>。完整 7,200 题、1 epoch、单卡 batch 1 × 累积 8，预期 900 次更新。</p>
<p>若没有课程模型目录，把 <code>--model</code> 改为 <code>Qwen/Qwen2.5-VL-7B-Instruct</code>，首次运行会下载权重。显存不足时检查图像设置；改变设置后需记录下来。合并需要足够主机内存与磁盘：</p>
${code(commands.merge)}
<p><strong>核对：</strong>合并目录包含完整权重与 processor。下一步使用这份合并模型推理，与课堂 SFT900 merged 的对照一致。</p>
</details>
<details><summary>5 · 对照：固定同一候选，分别预测 Base 与 SFT</summary>
<p>先完成第 4 步。保持问题、图片、提示、生成长度和评估规则相同：</p>
${code(commands.predict)}
<p>再从保存的预测计算指标：</p>
${code(commands.evaluate)}
<p><strong>核对：</strong>两组均覆盖 1,658 道题；比较各报告的 <code>reranker</code> 指标与回退比例。报告中的 <code>baseline</code> 指 ColQwen2 原排序。对同一 top-5 集合重排，Recall@5 不会变化。用验证集选模型，测试集用于最终比较。</p>
<p><strong>没有 GPU：</strong>直接用已保存的真实预测重算分数，只需要 CPU：</p>
${code(commands.existing)}
<p>提交一段任务说明、一题完整流程、自己的配置和两个指标报告，并解释一个改善或退步案例。数据缺失或评估器报错时核对文件版本，保留失败题。</p>
</details>
<details><summary>6 · 可选补充：这些数据与教师证据怎样准备</summary>
<p><code>data/train/pages.jsonl</code> 存页面，<code>data/splits/train_queries.jsonl</code> 存隔离后的训练题，<code>data/train_candidates.jsonl</code> 存候选及标签，<code>data/sft-flash-lite.jsonl</code> 存最终示范。</p>
<p>下面只检查 4 题标注计划，不发 API 请求；K 张图通常需要 K 次逐页证据请求 + 1 次精炼。</p>
${code(commands.annotation)}
<p><strong>核对：</strong>显示 <code>mode: dry-run</code> 与请求估算。直接复用课程现有 7,200 条，无需再次标注。</p>
<details><summary>从头复现：下载、候选检索与自费试标注</summary>
<p>只在新的项目副本运行：准备脚本会真实下载并写入 <code>data/</code>。原始训练源约 49 GB，评估源约 1.60 GB，另需图片和索引空间；转换需要较大 CPU 内存，检索需要 GPU。</p>
${code(commands.prepare)}
<p>准备脚本完成转换、文档划分和与测试集的元数据重叠排除。训练的 <code>--training</code> 允许补正确页；测试必须保留真实召回，不能添加该标志。</p>
<p>教师标注仅用于自己的新训练数据。将 <code>OPENROUTER_API_KEY</code> 保存在被 Git 忽略的 <code>.env</code>；先去掉 <code>--execute</code> 查看计划，添加它才会发出付费请求：</p>
${code(commands.paidAnnotation)}
<p>这里只试标 4 题，费用上限 1 美元。复用同一缓存目录，人工核对年份、单位和页面证据，记录排除项后再扩大数据量。试标结果只含 4 题，不是课堂 7,200 条完整训练集。</p>
</details>
</details>`,
    },
    en: {
      sft: String.raw`
<p class="lead"><strong>SFT (Supervised Fine-Tuning) continues model training using demonstrated answers.</strong> We start from Qwen2.5-VL-7B-Instruct, which already processes images and text and follows instructions, and teach it to produce complete rankings supported by page evidence.</p>
<p class="callout" id="sft-visual-input"><strong>The student still receives the question and real candidate images.</strong> The teacher’s image analysis and text refinement prepare the assistant target. Here that target becomes the completion, while the images field loads the real pages. SFT remains multimodal.</p>
<h3>1 · What does the model learn?</h3>
<div class="grid-two">
<div class="card"><h4>Parameters and tokens</h4><p><strong>Parameters</strong> are numerical values inside the model. Training changes them, which changes output probabilities. A <strong>token</strong> is a unit of text: a word, part of a word, or punctuation. One token need not equal one character.</p></div>
<div class="card"><h4>Prompt and completion</h4><p>The <strong>prompt</strong> contains task instructions, the question and candidate images. The <strong>completion</strong> is the desired assistant answer. We supervise evidence text, formatting and the complete ranking, including positions after the first.</p></div>
</div>
<p>Without this project's SFT, the original instruction model can already try ranking pages from the same prompt: this is <strong>Base</strong>. SFT adapts its existing capabilities to our task; whether it helps requires comparison against Base on new questions.</p>
<h3>2 · Separate the example into input and target</h3>
<p>Reuse the REITs question. This illustration abbreviates the evidence and retains the real target ranking:</p>
<div class="code-block"><pre><code>prompt: Question "In what year did REITS start in the US?"
        + candidate images 1, 2, 3, 4, 5 + output instructions
completion: &lt;think&gt;Image 3 states that US REITs began in 1960;
             the other pages lack this date...&lt;/think&gt;
            &lt;answer&gt;[3, 2, 1, 5, 4]&lt;/answer&gt;
positive_ids: [3]  # Validation/scoring metadata; excluded from the prompt</code></pre></div>
<p><code>positive_ids</code> contains the positions of dataset gold pages among these candidates. The framework also has a field called <code>labels</code>: <strong>target token IDs</strong> for loss computation. These are different arrays. When ranking a new question, the model receives only the prompt and images.</p>
<h3>3 · How does one demonstration change parameters?</h3>
<ol>
<li><strong>Processor:</strong> encode text as token IDs and prepare visual inputs, preserving the correspondence between images and candidate numbers.</li>
<li><strong>Forward pass:</strong> predict the next token at each position. Training uses the preceding correct target text as the prefix, called <em>teacher forcing</em>. A causal mask prevents each position from accessing later answer tokens.</li>
<li><strong>Cross-entropy (CE):</strong> compare predicted probabilities with correct tokens, scoring only the final assistant's evidence, formatting and complete ranking. Prompt, image-marker and padding positions are excluded as targets but still participate as context.</li>
<li><strong>Backward pass:</strong> calculate gradients: how small changes to trainable parameters affect loss. Accumulate gradients across examples as configured.</li>
<li><strong>Optimizer:</strong> AdamW adjusts LoRA parameters using gradients, historical statistics and the learning rate. Clear gradients, process the next batch and eventually save the parameters.</li>
</ol>
<div class="formula">Loss for one target token = −ln p<br>p is the predicted probability of the correct next token</div>
<p>Worked example: increasing p from 0.1 to 0.8 lowers loss from 2.303 to about 0.223. Training aggregates loss over supervised tokens. <strong>Lower loss means closer imitation; test ranking quality still needs measurement.</strong> More teacher-like evidence wording can also reduce loss. Use the probability slider below to calculate it.</p>
<p>Training can compute predictions at different positions in parallel. At inference, there is no correct target prefix: the model continues from its own generated tokens. This helps explain why training behavior and actual ranking can differ.</p>
<h3>4 · LoRA: fine-tune fewer parameters</h3>
<p><strong>LoRA (Low-Rank Adaptation)</strong> freezes an existing large matrix W and learns two small matrices A and B, whose product supplies an update. Fewer parameters need training and saving, while the base model must still be loaded and used in computation. <a href="https://arxiv.org/abs/2106.09685" target="_blank" rel="noreferrer">Original LoRA paper</a></p>
<div class="formula">W<sub>effective</sub> = W + (α/r)BA<br>W: d<sub>out</sub> × d<sub>in</sub>; A: r × d<sub>in</sub>; B: d<sub>out</sub> × r</div>
<p><code>d_in</code> and <code>d_out</code> are input and output dimensions. <code>r</code> is the small intermediate dimension controlling update capacity; <code>α/r</code> scales the change. Applying A and then B produces an update with exactly the shape of W.</p>
<div class="card"><h4>Worked example: one 1,000 × 1,000 matrix</h4><p>Full training updates 1,000,000 numbers. With r=8, A and B contain <strong>8 × 1,000 + 1,000 × 8 = 16,000</strong>, or 1.6% as many. These are invented teaching dimensions, not a real Qwen layer. Change r in the calculator below to compare capacity.</p></div>
<p>The recorded run used <strong>r=8, α=32 and 20,185,088 trainable parameters</strong>. LoRA targets selected linear layers in Qwen's language module; base weights and the vision module are frozen. <strong>Frozen means parameters are not updated; the model still processes images.</strong> This changes the reranker while keeping ColQwen2's weights unchanged. LoRA is a training technique; RAG is a retrieve-then-generate workflow.</p>
<h3>5 · Why do 7,200 examples produce only 900 updates?</h3>
<p>A <strong>batch</strong> is a set of examples processed together. <strong>Gradient accumulation</strong> combines gradients from several small batches before updating, reducing memory needed per batch. An <strong>epoch</strong> is one pass through the selected training set.</p>
<table><thead><tr><th>Setting</th><th>Completed four-GPU run</th><th>Classroom single-GPU default</th></tr></thead><tbody>
<tr><td>Per-GPU batch × GPUs × accumulation</td><td>1 × 4 × 2</td><td>1 × 1 × 8</td></tr>
<tr><td>Effective batch: examples per update</td><td>8</td><td>8</td></tr>
<tr><td>7,200 examples × 1 epoch ÷ 8</td><td>900 updates</td><td>900 expected updates</td></tr>
</tbody></table>
<p>Four GPUs each process one example and combine gradients after two rounds; one GPU processes eight examples before updating. Check accumulation whenever GPU count changes.</p>
<h3>6 · How do we use the trained model?</h3>
<p>An <strong>adapter</strong> stores the learned LoRA changes; load it with its matching base model for inference. <strong>Merging</strong> incorporates those changes into the base weights and saves a complete model, without further training. Both forms accept the same question and candidate images and generate evidence plus a ranking.</p>
<div class="formula">Train → save adapter (or merge) → retrieve new candidates with ColQwen2 → run Qwen to rank them</div>
<p>SFT happens beforehand; each use requires inference. Fix candidates and inference settings and compare Base versus SFT on independent test questions. The practice appendix provides training, adapter-loading, merging and evaluation commands.</p>
<details><summary>Configuration and code: map teaching steps to the implementation</summary>
<p><code>training/data.py</code> splits messages into prompt and completion. <code>completion_only_loss=true</code> supervises the completion; <code>assistant_only_loss=false</code> therefore does not make user questions targets. In <code>training/sft.py</code>, SFTTrainer manages encoding, loss, backpropagation and updates. <code>training/common.py</code> selects LoRA layers and freezes vision parameters.</p>
<p>Shared settings: learning rate 10⁻⁴, AdamW, cosine schedule, warmup ratio 0.03, BF16, SDPA, and 200,704–401,408 pixels per image. The recorded four-GPU run disables gradient checkpointing and saves every 50 steps; single-GPU defaults enable it and save each epoch. Gradient checkpointing recomputes intermediate values to save memory; a checkpoint saves training progress.</p>
<p class="source-note"><a href="assets/sources/training-config-snapshot.json" target="_blank">Recorded configuration snapshot</a> · <a href="assets/sources/src__docreranker__training__data.py.txt" target="_blank">Data and input construction</a> · <a href="assets/sources/src__docreranker__training__sft.py.txt" target="_blank">SFT source</a>; classroom defaults: <code>configs/sft.json</code>.</p>
</details>`,
      grpo: String.raw`
<p class="lead"><strong>GRPO (Group Relative Policy Optimization) learns from score differences between the model's own attempts.</strong> In this teaching workflow, we start from SFT-trained Qwen and continue improving its candidate-page rankings.</p>
<div class="callout">First use small invented examples to understand rewards and updates. The second half explains our completed GRPO512 training run and its results on the same test set.</div>
<h3>1 · From imitating one demonstration to comparing attempts</h3>
<p>A <strong>policy</strong> is the model's probability distribution over the next token given its context. Sampling from those probabilities can produce different evidence summaries and rankings for one question.</p>
<ol>
<li><strong>Fix the question and candidate images:</strong> load the SFT policy and supply the same kind of prompt used at inference.</li>
<li><strong>Generate a group:</strong> sample G complete answers, each with evidence and a ranking. <strong>G counts attempts; K=5 counts candidate pages. They are independent.</strong></li>
<li><strong>Score each answer:</strong> use positive_ids mapped from dataset gold labels to check formatting and relevant-page ranks. Labels never enter the model prompt.</li>
<li><strong>Compare and update:</strong> compare rewards with the group mean to calculate advantages. Training tends to increase positive-advantage answer probabilities and decrease negative-advantage ones. Sample again and continue learning.</li>
</ol>
<div class="formula">Sample answers to one question → reward each → compute group-relative advantages → update policy → try again</div>
<h3>2 · How does a ranking become a reward?</h3>
<p>Use the classroom scoring example: total reward = format reward + ranking reward. Valid formatting requires nonempty evidence tags and each candidate number 1–5 exactly once. Invalid permutations earn zero for both terms.</p>
<div class="formula">Ranking reward = Σ(1/rank³) over relevant pages in the generated order<br>÷ Σ(1/rank³) when all relevant pages come first</div>
<p><code>Σ</code> means to add the relevant-page scores. The cubic discount emphasizes earlier positions; the denominator makes an ideal ranking score 1. “Rank” means output position, not candidate ID.</p>
<div class="card"><h4>Worked example: gold = {2, 4}</h4><p><code>[2, 1, 4, 3, 5]</code> puts relevant pages at ranks 1 and 3. Its gain is <code>1 + 1/27</code>; the ideal gain is <code>1 + 1/8</code>. Ranking reward is <strong>224/243 ≈ 0.922</strong>. With valid evidence structure and formatting, add 1 for about <strong>1.922</strong>. A valid <code>[2, 4, 1, 3, 5]</code> can earn 2; <code>[2, 2, 4, 3, 5]</code> earns 0 because it repeats an ID.</p></div>
<h3>3 · Why subtract the mean for the same question?</h3>
<p>An <strong>advantage</strong> measures how good an attempt is relative to others at that question. This example normalizes using the population standard deviation:</p>
<div class="formula">A<sub>i</sub> = (R<sub>i</sub> − μ) / (σ + ε)<br>R<sub>i</sub>: reward for answer i; μ: group mean; σ: population SD; ε: a small positive value preventing division by zero</div>
<p>Invent rewards <code>[1, 2]</code>: the mean is 1.5 and population SD is 0.5. Ignoring tiny ε, advantages are <strong>[−1, +1]</strong>. Attempts are compared within each question even when questions differ in difficulty. Frameworks may use another SD convention; the interaction below explicitly uses population SD without claiming it matches the training implementation.</p>
<p>Group scores supply the baseline, so GRPO <strong>does not need a separate value/critic model predicting expected reward</strong>. This example can also compute rewards directly from labels and rules. With an outcome reward for a complete answer, its advantage can guide updates to each generated token in that answer. <a href="https://arxiv.org/html/2402.03300v3#S4.SS1" target="_blank" rel="noreferrer">Original GRPO paper</a></p>
<h3>4 · How do we avoid overreacting to one sample group?</h3>
<p>The <strong>old policy</strong> is the snapshot that generated this group. For the same token and prefix, calculate:</p>
<div class="formula">ρ = current-policy probability / old-policy probability</div>
<p>Old probability 0.20 and current probability 0.30 give ρ=1.5. With an illustrative clipping interval [0.8, 1.2], a positive-advantage term gains no extra proportional benefit from pushing this ratio to 1.5. <strong>Clipping limits incentives in the objective; it does not strictly guarantee every probability changes by at most 20%.</strong></p>
<p>The <strong>reference policy</strong>, often the SFT model, is a behavioral anchor with a different role from the sampling policy. If enabled, a <strong>KL penalty</strong> imposes a larger cost for a greater distribution shift from that reference; coefficient β sets its strength. Clipping controls this update's incentive; KL controls departure from the reference. <a href="https://huggingface.co/docs/trl/grpo_trainer#computing-the-loss" target="_blank" rel="noreferrer">TRL explanation</a></p>
<h3>5 · What can the scores tell us?</h3>
<table><thead><tr><th>Comparison</th><th>SFT</th><th>GRPO</th></tr></thead><tbody>
<tr><td>Training material</td><td>Questions, images and fixed demonstrations</td><td>Questions, images and gold labels for scoring; multiple answers generated during training</td></tr>
<tr><td>Learning signal</td><td>Cross-entropy on target tokens</td><td>Relative advantages from rewards</td></tr>
<tr><td>What becomes more likely?</td><td>Demonstrated answers</td><td>Answers with relatively higher group rewards</td></tr>
<tr><td>Shared evaluation</td><td colspan="2">Measure ranking quality on the same test set and check whether page evidence is factual</td></tr>
</tbody></table>
<p>Equal group rewards give zero relative-reward advantages; an enabled KL term may still contribute a constraint signal. The best attempt in a group can still be wrong. This reward checks formatting and ranking; <strong>it does not verify the truth of evidence text</strong>. Higher training reward need not improve independent test metrics. Change the two outputs below to explore these differences.</p>`,
      lab: String.raw`
<p class="lead">Start with the prepared data and labels. Open the six steps as needed to inspect an example, run SFT and evaluate the comparison.</p>
<p>Run commands from <code>Active/class</code>; save results under <code>outputs/student-*</code>. Steps 1–3 do not train a model. New training and prediction in steps 4–5 require the course GPU resources, model weights and full image assets. The offline website package is for presentation.</p>
<details><summary>1 · Environment: locate the project and Python</summary>
<p>Enter the project from the workspace root. Skip the first line if already in <code>class</code>.</p>
${code(commands.environment)}
<p><strong>Check:</strong> you see the Python version and command options. Reuse the course <code>.venv</code>, or create your own environment with Python 3.10+:</p>
${code(commands.install)}
<p>For your own environment, replace subsequent <code>.venv/bin/python</code> commands with <code>.venv-student/bin/python</code>. <code>python -m</code> runs a project module; options such as <code>--input</code> specify files or settings. If <code>docreranker</code> cannot be found, check the interpreter and installation directory.</p>
</details>
<details><summary>2 · Data: read the real REITs example from class</summary>
<p>Find the same query_id in the source and SFT files. Compare dataset gold IDs, candidate positions and the target. JSONL stores one JSON object per line; <code>python -</code> runs the code between the two <code>PY</code> markers.</p>
${code(commands.read)}
<p><strong>Check:</strong> the positive input position is <code>[3]</code> and the target ranking is <code>[3, 2, 1, 5, 4]</code>. Map each position back to its page_id; every image-existence result should be <code>True</code>. Copying JSONL does not copy the images referenced by its absolute paths.</p>
</details>
<details><summary>3 · Before training: validate the existing 7,200 SFT examples</summary>
${code(commands.dryrun)}
<p><strong>Check:</strong> <code>stage: sft</code>, <code>dry_run: true</code>, and <code>source_examples: 7200</code>. This checks images, labels, rankings and configuration without loading a model or producing weights. This command's exclusion option compares query_id only; course preparation also checked documents and question text. Passing validation does not establish sufficient GPU memory.</p>
</details>
<details><summary>4 · SFT: train on one GPU, save the adapter, then merge</summary>
<p>Run in an allocated single-GPU environment. This example selects available GPU 0. If a scheduler already sets <code>CUDA_VISIBLE_DEVICES</code>, keep its setting and remove the override at the start of the command. Use a new, empty output directory for each fresh run.</p>
${code(commands.train)}
<p><strong>Check:</strong> <code>outputs/student-sft</code> contains an adapter, processor and <code>docreranker_training.json</code>, with <code>dry_run: false</code> and <code>global_step &gt; 0</code>. With 7,200 examples, one epoch, batch 1 and accumulation 8 on one GPU, expect 900 updates.</p>
<p>Without the course model directory, change <code>--model</code> to <code>Qwen/Qwen2.5-VL-7B-Instruct</code>; the first run downloads weights. If GPU memory runs out, inspect image settings and document any changes. Merging needs sufficient host memory and disk space:</p>
${code(commands.merge)}
<p><strong>Check:</strong> the merged directory contains full weights and a processor. The next step uses this merged model for inference, matching the classroom’s SFT900 merged comparator.</p>
</details>
<details><summary>5 · Comparison: predict with Base and SFT on identical candidates</summary>
<p>Complete step 4 first. Keep questions, images, prompts, generation length and evaluation rules the same:</p>
${code(commands.predict)}
<p>Then calculate metrics from the saved predictions:</p>
${code(commands.evaluate)}
<p><strong>Check:</strong> both cover 1,658 questions. Compare the <code>reranker</code> metrics and fallback rates in the reports. Each report's <code>baseline</code> means the original ColQwen2 order. Reranking an unchanged top-5 set cannot change Recall@5. Select models using validation data and use the test set for the final comparison.</p>
<p><strong>No GPU:</strong> rescore the existing real predictions using CPU only:</p>
${code(commands.existing)}
<p>Submit a task explanation, one complete example, your configuration and both metric reports. Explain an improved or worsened case. If files are missing or evaluation fails, check data versions and retain failed questions.</p>
</details>
<details><summary>6 · Optional background: preparing data and teacher evidence</summary>
<p><code>data/train/pages.jsonl</code> stores pages; <code>data/splits/train_queries.jsonl</code> stores isolated training questions; <code>data/train_candidates.jsonl</code> stores candidates and labels; <code>data/sft-flash-lite.jsonl</code> stores final demonstrations.</p>
<p>The command below checks an annotation plan for four questions without API requests. K images normally require K page-evidence requests plus one refinement request.</p>
${code(commands.annotation)}
<p><strong>Check:</strong> output shows <code>mode: dry-run</code> and estimated request counts. Reuse the course's existing 7,200 examples; no repeat annotation is needed.</p>
<details><summary>Starting from scratch: downloads, retrieval and paid pilot annotation</summary>
<p>Run only in a fresh project copy: the preparation script downloads data and writes to <code>data/</code>. Raw training data is about 49 GB and evaluation data about 1.60 GB, plus space for images and indexes. Conversion needs substantial CPU memory; retrieval needs a GPU.</p>
${code(commands.prepare)}
<p>Preparation handles conversion, document splitting and metadata overlap checks against the test set. The training <code>--training</code> flag permits adding correct pages. Test candidates must preserve actual retrieval; do not add that flag to test search.</p>
<p>Annotate only your own new training data. Store <code>OPENROUTER_API_KEY</code> in a Git-ignored <code>.env</code>. First remove <code>--execute</code> to inspect the plan; including it sends paid requests:</p>
${code(commands.paidAnnotation)}
<p>This pilot covers four questions with a $1 cost ceiling. Reuse the same cache directory. Inspect years, units and page evidence, and record exclusions before scaling up. Four pilot examples do not constitute the complete 7,200-example classroom dataset.</p>
</details>
</details>`,
    },
  };
})();
