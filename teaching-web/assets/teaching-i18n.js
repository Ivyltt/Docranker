// Bilingual interface and cases; raw model responses remain unchanged.
window.CLASS_TRANSLATIONS = {
  "en": {
    "cases": {
      "eval:PH_2016.06.08_Economy-Final.pdf:0": {
        "title": "Valid format can still give the same ranking score",
        "query": "According to the report, how do 5% of the Latinos see economic upward mobility for their children?",
        "lesson": "Base produced two think blocks, so the system rejected its proposed order. SFT passed the output checks, but the order used for scoring was still the same. This example separates format validity from ranking scores; its answer has not been manually fact-checked.",
        "selectionReason": "The first question in the official evaluation file, selected to show different output formats with identical scores.",
        "review": {
          "summary": "This example was not included in the existing manual visual review. It reproduces the saved inputs, outputs, rankings, and scores under the original labels.",
          "notes": [
            "Do not treat the model's explanation as a manually verified answer."
          ],
          "rankingFinding": null,
          "baseFindings": [],
          "sftFindings": [],
          "scope": "No manual review conclusion is available."
        }
      },
      "eval:05-03-18-political-release.pdf:5": {
        "title": "A better page order can still contain a chart-reading error",
        "query": "How many Demoncratic people in the survey of U.S. adults conducted April 25- May 1, 2019 said neither the Republican Party nor the Democratic Party  has ‘high ethical standards'?",
        "lesson": "SFT moved the only retrieved gold page to first place, raising Recall@1 from 0 to 1/2. The saved manual review also found errors involving the population, chart column, and year in the question.",
        "selectionReason": "An already reviewed example retained from the earlier replay.",
        "review": {
          "summary": "Report the ranking improvement under the original labels separately from the factual accuracy of the explanation. This example also requires noting the conflicting years in the question and source.",
          "notes": [
            "The question says 2019, but the survey footnote on the candidate pages explicitly says 2018. This is a mismatch between the source question and its material. Keep the original question and do not present the 2018 figure of 18% as a verified answer for 2019."
          ],
          "rankingFinding": "Under the fixed original labels, SFT moved the only retrieved gold page, input Page 2, to first place, increasing Recall@1 from 0 to 0.5. The other gold page, record page 16, was not retrieved; it remains in the denominator and was not visually checked in this review.",
          "baseFindings": [
            "Base's original explanation says input Page 1 contains the 'neither' statistic. That page actually compares the public's overall ratings of the two parties and has no cross-tabulation for 'neither'.",
            "Base outputs only [1], rather than a complete permutation of all five candidates. Its official score therefore uses the retrieval-order fallback; this is not a valid model ranking."
          ],
          "sftFindings": [
            "SFT says 47% of the public thinks neither party has high ethical standards. In the chart, 47% refers to people who think only one party meets that standard; the public's 'neither' figure is 25%.",
            "The question asks about Democratic respondents. The 2018 chart gives 18% for Democrat/Neither. SFT confuses the population and reads the wrong column."
          ],
          "scope": "Page-value, category and population checks still apply to the 47%/58% claims retained by merged SFT. Not all new model explanations have been checked sentence by sentence; no full-test explanation-accuracy rate was measured."
        }
      },
      "eval:05-03-18-political-release.pdf:0": {
        "title": "When the score falls, check both labels and source pages",
        "query": "Among the adults conducted the survey on April 25 - May 1 2018, how many adults rated Trump's government ethical standards as poor? ",
        "lesson": "SFT's Recall@1 fell under the original labels. Manual review found that its first-ranked page was relevant but missing from the gold labels. Its explanation also replaced the correct 'poor' figure of 36% with the combined 'poor or not good' figure of 58%. These are three separate findings.",
        "selectionReason": "An already reviewed example retained from the earlier replay.",
        "review": {
          "summary": "The lower score under the original labels, actual page relevance, and numerical error in the explanation are three distinct facts. Report all three and preserve the original scores and labels.",
          "notes": [
            "Input Page 2 / record page 7 and input Page 3 / record page 8 are both negative under the original labels, yet directly contain the 36% 'poor' figure or the same chart for the same question and survey. The relevance labels omit evidence pages; this is not an image-conversion page-number error.",
            "The question says 'how many', but the visible material gives percentages, not respondent counts for that answer. This review checks only the percentages explicitly shown on the pages."
          ],
          "rankingFinding": "The original gold pages are record pages 2 and 3 (input Pages 1 and 4). SFT ranks input Page 3 / record page 8 first. Because it is not labeled gold, Recall@1 is 0 under the original labels. However, it directly contains the ethics-standards chart for the Total population in the same survey; the label alone does not establish that it is irrelevant.",
          "baseFindings": [
            "Base says input Page 2 has no specific numbers, contradicting that page's explicit 'poor: 36%' figure.",
            "For input Page 3, Base describes 58% as the combined 'poor or not good' figure, which identifies the category more accurately than SFT. However, it emits multiple think blocks and no answer tag, so the system still falls back because of invalid format.",
            "The system did not accept Base's proposed [3,4,1,2,5]. The order actually used for scoring, [1,2,3,4,5], comes from the original retrieval order."
          ],
          "sftFindings": [
            "SFT labels 58% as 'poor'. The original chart and the text on input Page 2 give 36% for 'poor'; 58% combines 'not good' and 'poor'.",
            "SFT says input Page 2 and other pages lack the exact 'poor' percentage. In fact, the first paragraph of input Page 2 / record page 7 explicitly states 'poor (36%)'."
          ],
          "scope": "Page-value, category and population checks still apply to the 47%/58% claims retained by merged SFT. Not all new model explanations have been checked sentence by sentence; no full-test explanation-accuracy rate was measured."
        }
      },
      "eval:2020.acl-main.45.pdf:0": {
        "title": "GRPO improvement: move the gold page to first place",
        "query": "How does the paper propose to calculate the coefficient α for the Weighted Cross Entropy Loss?",
        "lesson": "With the same five images, SFT places gold image 1 second and GRPO moves it first. Recall@1 rises from 0 to 1 under the original labels. This measures evidence-page position, not whether the prose explains α correctly.",
        "selectionReason": "The first query_id satisfying the stored improvement/regression condition on the same 1,658 questions.",
        "review": {
          "summary": "Compare the stored original labels, rankings and raw outputs. Model explanations are not manually verified answers.",
          "notes": [],
          "scope": "No new sentence-by-sentence factual review of these two new model explanations."
        }
      },
      "eval:2019713412.pdf:1": {
        "title": "GRPO regression: average gains do not improve every query",
        "query": "Can an accused appeal a conviction and sentence resulting from plea bargaining in Malaysia, and if so, on what grounds?",
        "lesson": "Image 2 is gold under the original labels. SFT puts it first; GRPO instead puts image 1 first, lowering Recall@1 from 1 to 0. Both outputs have valid format, yet their ranking quality differs.",
        "selectionReason": "The first query_id satisfying the stored improvement/regression condition on the same 1,658 questions.",
        "review": {
          "summary": "Compare the stored original labels, rankings and raw outputs. Model explanations are not manually verified answers.",
          "notes": [],
          "scope": "No new sentence-by-sentence factual review of these two new model explanations."
        }
      }
    },
    "code": {
      "data": {
        "title": "Data records: questions and relevant-page labels",
        "explanation": "Training data takes all relevant pages from positive_passages; evaluation data takes them from each question's page_id. Both become records with query_id, query, doc_names, and positive_page_ids. page_key binds the source, document, and original page number into a stable identity. Evaluation page numbers are zero-based offsets within a document and are range-checked; output candidate indices start at 1. The full conversion code also saves page images, checks that gold pages exist, and writes pages.jsonl and queries.jsonl."
      },
      "prompt": {
        "title": "Input format: a question and numbered images",
        "explanation": "The shared prompt requests an evidence explanation and a complete permutation. The student's inference input does not expose relevance labels or retrieval scores."
      },
      "retrieval": {
        "title": "Retrieval: score question–page similarity",
        "explanation": "For each query token, find its best-matching page token, then sum those maximum scores. Use the resulting page scores to select the top k."
      },
      "retrievalCandidates": {
        "title": "Candidates: how training and evaluation differ",
        "explanation": "With training=False, take only the top k by score. Only training=True may use known gold labels to construct positives and hard negatives. The code prohibits gold injection for validation or evaluation."
      },
      "annotationShuffle": {
        "title": "Before annotation: shuffle candidates and remap indices",
        "explanation": "First set the target priority using relevance labels and retrieval scores, then shuffle the input pages. Recompute target and positives as 1-based positions in the shuffled input."
      },
      "annotation": {
        "title": "Teacher annotation: page evidence, explanation, target",
        "explanation": "The teacher inspects each page image and compiles an explanation. The code writes the target order using the original relevance labels. The teacher can also misread a chart, so its explanations need spot checks."
      },
      "sftData": {
        "title": "Training examples: separate prompt and completion",
        "explanation": "SFT uses the final assistant message as the completion. Earlier messages and images form the prompt. With completion_only_loss, only the answer portion contributes to the supervised loss."
      },
      "sftTrain": {
        "title": "SFT: load the model, add LoRA, train, and save",
        "explanation": "SFTTrainer receives multimodal examples and a LoRA configuration. After training, it saves the adapter and processor. The base model is already pretrained; this stage fine-tunes it for our task."
      },
      "merge": {
        "title": "Merge LoRA: add the learned update to base weights",
        "explanation": "merge_and_unload incorporates the learned LoRA update into the base model. It performs no additional training. Base plus adapter and a merged model are two ways to load the trained model."
      },
      "inference": {
        "title": "Parse the output: accept a valid order or fall back",
        "explanation": "Model output must pass strict parsing. On failure, the ranking actually used is the original candidate order. An array that appears in the raw output but fails validation must not be used for scoring."
      },
      "evaluation": {
        "title": "Evaluation: compute metrics for each question",
        "explanation": "Recall keeps every gold page in its denominator. MRR uses the position of the first correct page. The ideal-ranking denominator for nDCG also includes gold pages missed by retrieval."
      }
    },
    "glossary": [
      {
        "term": "RAG",
        "en": "Retrieval-Augmented Generation",
        "definition": "Retrieve useful material, then give it to a model to answer the question.",
        "example": "Find the relevant report page before generating an answer."
      },
      {
        "term": "Retrieval",
        "en": "ColQwen2 in this project",
        "definition": "Select a small set of candidate pages by their similarity to the question.",
        "example": "Score the available pages and keep the top 5."
      },
      {
        "term": "Reranking",
        "en": "Reorder the candidates",
        "definition": "Compare the retrieved candidates again to put more useful pages first.",
        "example": "[A,B,C] becomes [B,A,C]; a missed page F cannot enter this list."
      },
      {
        "term": "Candidate index",
        "en": "Local page number",
        "definition": "A page's 1-based position in the model's input image list.",
        "example": "An output of 3 means the third input image, not necessarily PDF page 3."
      },
      {
        "term": "Gold pages",
        "en": "Positive / relevant-page labels",
        "definition": "All pages labeled relevant in the dataset, including pages that retrieval misses.",
        "example": "If gold={B,F}, the Recall denominator stays 2 even when F is absent."
      },
      {
        "term": "Prompt",
        "en": "Model input",
        "definition": "The instructions, question, and content the model sees before generating its output.",
        "example": "In this project: a question, five page images, and the required output format."
      },
      {
        "term": "Completion",
        "en": "Target assistant output",
        "definition": "The assistant response the model learns to generate.",
        "example": "A brief evidence explanation followed by the complete candidate order."
      },
      {
        "term": "Token",
        "en": "Sequence unit",
        "definition": "One unit in the sequence processed by a model; a text token need not be a whole word.",
        "example": "Training predicts the target completion one token at a time."
      },
      {
        "term": "SFT",
        "en": "Supervised Fine-Tuning",
        "definition": "Further train an existing model using examples of inputs and target outputs.",
        "example": "Our saved run uses 7,200 evidence-and-ranking demonstrations."
      },
      {
        "term": "Loss",
        "en": "Training error signal",
        "definition": "A measure of disagreement between predictions and targets that training tries to reduce.",
        "example": "Giving the correct next token a higher probability lowers its cross-entropy loss."
      },
      {
        "term": "LoRA",
        "en": "Low-Rank Adaptation",
        "definition": "Fine-tune small added matrices while freezing the large base weight matrices.",
        "example": "This SFT run uses rank 8 and alpha 32."
      },
      {
        "term": "GRPO",
        "en": "Group Relative Policy Optimization",
        "definition": "Update a model by comparing rewards for several outputs generated for the same question.",
        "example": "Increase the probability of outputs that do better within their group."
      },
      {
        "term": "Reward",
        "en": "Output score",
        "definition": "A score computed by the training program to express a preference among outputs.",
        "example": "A reward may score valid format and ranking without checking factual explanations."
      },
      {
        "term": "Advantage",
        "en": "Relative reward signal",
        "definition": "How an output's reward compares with its group's average, often normalized.",
        "example": "If every output has the same reward, their relative reward advantages are zero."
      },
      {
        "term": "Recall@K",
        "en": "Recall at K",
        "definition": "The number of gold pages in the first K positions divided by the total number of gold pages.",
        "example": "Two gold pages, with one ranked first: Recall@1 = 1/2."
      },
      {
        "term": "Fallback",
        "en": "Keep retrieval order",
        "definition": "Use the original retrieval order when model output fails the complete-permutation checks.",
        "example": "[2,2,3] repeats an index and is not a valid order for three pages."
      }
    ]
  },
  "zh": {
    "glossary": [
      {
        "term": "RAG",
        "en": "Retrieval-Augmented Generation",
        "definition": "检索增强生成：先找有用资料，再交给模型回答。",
        "example": "先找到相关报表页，再让模型根据该页回答问题。"
      },
      {
        "term": "检索",
        "en": "Retrieval",
        "definition": "按页面与问题的相似程度，选出少量候选页。",
        "example": "本项目用 ColQwen2 给页面打分，再取前 5 页。"
      },
      {
        "term": "重排序",
        "en": "Reranking",
        "definition": "重新比较检索得到的候选，让更有用的页靠前。",
        "example": "[A,B,C] 变成 [B,A,C]；漏掉的 F 无法进入这个列表。"
      },
      {
        "term": "候选编号",
        "en": "Local Candidate Index",
        "definition": "页面在本次输入图片列表中从 1 开始的位置。",
        "example": "输出中的 3 指第 3 张输入图，不一定是 PDF 第 3 页。"
      },
      {
        "term": "相关页标签",
        "en": "Gold / Positive Pages",
        "definition": "数据集中标为相关的全部页面，包括检索没找到的页。",
        "example": "gold={B,F} 时，即使 F 未进入候选，Recall 的分母仍是 2。"
      },
      {
        "term": "输入",
        "en": "Prompt",
        "definition": "模型生成前看到的指令、问题和内容。",
        "example": "本项目是问题、五张页面图，以及输出格式要求。"
      },
      {
        "term": "目标输出",
        "en": "Completion",
        "definition": "希望模型学会生成的助手回答。",
        "example": "本项目是简短证据说明，加上全部候选编号的排列。"
      },
      {
        "term": "Token",
        "en": "Token",
        "definition": "模型处理序列的单位；文本 token 不一定是一个完整词。",
        "example": "训练时逐个预测目标回答的下一个 token。"
      },
      {
        "term": "SFT",
        "en": "Supervised Fine-Tuning",
        "definition": "监督微调：用输入和目标输出的示范，继续训练已有模型。",
        "example": "这次已保存实验使用 7,200 条证据与排序示范。"
      },
      {
        "term": "损失",
        "en": "Loss",
        "definition": "衡量预测与目标相差多少，训练尝试降低它。",
        "example": "正确的下一个 token 概率越高，对应的交叉熵损失越小。"
      },
      {
        "term": "LoRA",
        "en": "Low-Rank Adaptation",
        "definition": "冻结基础模型的大权重矩阵，只训练附加的小矩阵。",
        "example": "本次 SFT 使用 rank 8、alpha 32。"
      },
      {
        "term": "GRPO",
        "en": "Group Relative Policy Optimization",
        "definition": "同一道题生成多份输出，比较奖励后更新模型。",
        "example": "提高组内表现较好输出的生成概率。"
      },
      {
        "term": "奖励",
        "en": "Reward",
        "definition": "训练程序为输出计算的分数，用来表达偏好。",
        "example": "可以奖励格式和排序；它未必检查解释是否符合事实。"
      },
      {
        "term": "优势",
        "en": "Advantage",
        "definition": "一份输出的奖励相对组内平均值有多好，通常还会归一化。",
        "example": "全组奖励相同时，相对奖励优势都是 0。"
      },
      {
        "term": "Recall@K",
        "en": "Recall at K",
        "definition": "前 K 位命中的相关页数，除以全部相关页数。",
        "example": "总共 2 张相关页，第一位命中 1 张：Recall@1=1/2。"
      },
      {
        "term": "格式回退",
        "en": "Fallback",
        "definition": "模型输出不符合完整排列规则时，继续使用原检索顺序。",
        "example": "[2,2,3] 有重复编号，不是三张页面的合法排列。"
      }
    ]
  }
};
