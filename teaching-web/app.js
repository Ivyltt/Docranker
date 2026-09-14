/* Offline, bilingual, linear classroom. No model or API calls. */
(() => {
  'use strict';
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const D = window.CLASS_DATA, L = window.CLASS_LESSONS, I = window.CLASS_TRANSLATIONS, G = window.CLASS_FOUNDATIONS, H = window.CLASS_DESIGN;
  // Release metadata is separate from the fixed experiment evidence.
  const E = window.CLASS_EXPERIMENT;
  const SITE = window.CLASS_SITE || {};
  const read = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch (_) { return fallback; } };
  const save = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) {} };
  const requested = new URLSearchParams(location.search).get('lang');
  let lang = (requested || read('docreranker-language', 'zh')) === 'en' ? 'en' : 'zh';
  const t = (zh, en) => lang === 'zh' ? zh : en;
  const pct = n => `${(Number(n) * 100).toFixed(2)}%`;
  const num = n => Number(n).toFixed(4);
  const chapters = [
    ['rag','RAG 入门','RAG foundations','先找资料，再根据资料回答。','Find evidence, then answer from it.'],
    ['data','原始数据','The data','先看一道题。','Start with one question.'],
    ['retrieve','筛出候选页','Retrieve candidates','ColQwen2 先缩小范围。','ColQwen2 makes the shortlist.'],
    ['labels','构造监督答案','Build the target','模型要学的答案，从哪里来？','Where does the training target come from?'],
    ['sft','用 SFT 学会重排','Learn with SFT','用示例训练，再让模型排序。','Train on examples, then rerank.'],
    ['results','比较训练前后','Compare the results','从 Base 到 SFT，再到 GRPO。','From Base to SFT, then GRPO.'],
    ['grpo','GRPO 原理与结果','GRPO & results','继续训练：从奖励到测试结果。','Continued training: rewards to test results.'],
    ['lab','代码与实践','Code & practice','按需打开，跟着做。','Open a step and try it.']
  ];
  const aliases = {start:'rag',task:'retrieve',baseline:'results',evaluate:'results',sources:'lab'};
  let current = 'rag';
  const saved = read('docreranker-linear-progress-v2', []);
  const completed = new Set(Array.isArray(saved) ? saved.filter(id => chapters.some(c => c[0] === id)) : []);
  // One shared state object lets Chinese/English views keep the same exercise.
  const state = {shuffled:true,caseIndex:0,variant:'sft',gold:false,metric:'recall1',order:['A','B','C','D','E'],probability:20,rewardA:0,rewardB:1,source:'prompt',ragStep:0,scorePage:0,scoreStep:0,k:5,loraRank:8,quizAnswers:{},encodingMethod:3,teacherStep:0};
  const rawExample = {
    query_id:11570, query:'In what year did REITS start in the US?', domain:'SlideVQA',
    positive_passages:[{doc_name:'realestateinvestmenttrust-141212011105-conversion-gate01',page_id:4}],
    negative_passages:[{doc_name:'realestateinvestmenttrust-141212011105-conversion-gate01',page_id:1}]
  };
  const label = c => t(c[1], c[2]);
  const modelName = id => ({retrieval:t('ColQwen2 原检索','ColQwen2 retrieval'),base:t('Base · 未做 SFT','Base · no task SFT'),sft:'SFT900 merged',grpo:'SFT + GRPO512'})[id];
  const block = (text, caption='') => `<div class="code-block"><pre><code>${esc(text)}</code></pre>${caption ? `<div class="code-caption">${caption}</div>` : ''}</div>`;
  const details = (id, summary, content) => `<details id="${id}" class="more"><summary>${summary}</summary>${content}</details>`;
  const card = (heading, body) => `<article class="card"><h3>${heading}</h3><p>${body}</p></article>`;
  const strip = order => `<div class="rank-strip">${order.map(n=>`<span class="rank-chip">${t('图','Image')} ${n}</span>`).join('')}</div>`;
  const codeInfo = id => lang === 'en' ? {...D.code[id],...I.en.code[id]} : D.code[id];
  function snippet(id) {
    const c = codeInfo(id);
    return details(`code-${id}`, `${t('看代码','Read the code')}: ${esc(c.title)}`, `<p class="muted">${esc(c.explanation)}</p>${block(c.code, `${esc(c.path)} : ${c.startLine}–${c.endLine} · <a href="${esc(c.download)}" download>${t('完整源码','Full source')}</a>`)}`);
  }
  function flow(items) { return `<div class="linear-flow">${items.map((text,i)=>`${i?'<span class="linear-arrow" aria-hidden="true">→</span>':''}<div>${text}</div>`).join('')}</div>`; }
  function gallery(pages, gold=false, scores=false) {
    return `<div class="page-gallery">${pages.map(p=>`<article class="page-card ${gold&&p.isGold?'gold':''}"><button data-image="${esc(p.imageOriginal||p.image)}" data-caption="${esc(`${t('候选图','Candidate image')} ${p.index} · ${t('数据页号','Dataset page ID')} ${p.pageNumber}`)}" aria-label="${t('放大候选图','Enlarge candidate image')} ${p.index}"><img src="${esc(p.image)}" alt="${t('真实文档页面','Original document page')} ${p.pageNumber}" loading="lazy"></button><h4>${t('图','Image')} ${p.index} <span class="muted">↗</span></h4><p>${t('数据页号','Dataset page ID')} ${p.pageNumber}${scores?` · ${t('分数','score')} ${p.score.toFixed(2)}`:''}</p>${gold&&p.isGold?`<span class="gold-tag">${t('数据集标为相关','Labeled relevant')}</span>`:''}</article>`).join('')}</div>`;
  }
  // Work with copies: the saved rankings and relevance labels are never mutated.
  function retrievedPages() { return [...D.sftExample.pages].sort((a,b)=>a.originalRank-b.originalRank).map((p,i)=>({...p,index:i+1})); }
  function rag() {
    return G[lang].ragIntro + ragLab() + G[lang].ragAfter;
  }
  function ragLab() {
    return `<section class="lab-panel" id="rag-lab"><div class="lab-label"><h3>${t('一题走完整条 RAG 流程','One question through the RAG pipeline')}</h3><span class="chip">${t('点击步骤','Click a stage')}</span></div><div class="rag-stages">${[t('提出问题','Question'),t('检索候选','Retrieve'),t('重排证据','Rerank'),t('加入上下文','Augment'),t('生成回答','Generate')].map((name,i)=>`<button data-rag-step="${i}" aria-pressed="${state.ragStep===i}"><small>0${i+1}</small><strong>${name}</strong></button>`).join('')}</div><div id="rag-detail" class="rag-detail" aria-live="polite"></div></section>`;
  }
  function showRag() {
    const stages = [
      [t('问题 + 资料库','Question + collection'),t('美国 REITs 从哪一年开始？资料中有文字、图表和表格。先找能支撑回答的证据。','When did US REITs begin? The collection contains text, charts and tables. First, find supporting evidence.'),t('输入：问题与可搜索的页面。','Input: a question and searchable pages.'),t('输出：等待检索的问题。','Output: the question to retrieve for.')],
      ['ColQwen2',t('给每个可用页面打分，按分数选出少量候选。本例原检索顺序的数据页号是 [4, 5, 1, 21, 20]。分数怎么算，会在检索一节手算。','Score the available pages and select a shortlist. For this example, retrieval returns dataset page IDs [4, 5, 1, 21, 20]. We will calculate scores in the retrieval lesson.'),t('输入：问题向量与页面向量。','Input: question vectors and page vectors.'),t('输出：候选页、检索分数与原顺序。','Output: candidate pages, scores and retrieval order.')],
      [t('Qwen 重排 · 本项目训练的部分','Qwen reranking · the component we train'),t('模型同时查看问题和候选图片，生成证据说明与完整排列。SFT 用示范训练这个模型。本题证据页原本已在第一位，不需要假设它发生了改善。','The model reads the question and candidate images, then generates an evidence summary and complete ordering. SFT trains it on examples. Retrieval already placed the evidence first here, so this example need not improve.'),t('输入：问题、指令与候选图片。','Input: question, instructions and candidate images.'),t('输出：候选图编号的完整排列。','Output: a complete permutation of candidate image indices.')],
      [t('把证据放进回答提示','Add evidence to the answering prompt'),t('使用排在前面的页面作为上下文，连同原问题交给回答模型。这里相关页清楚写着 United States (1960)。','Use leading pages as context alongside the original question. Here the relevant page explicitly states United States (1960).'),t('输入：问题与排好的证据页。','Input: the question and ranked evidence pages.'),t('输出：带证据的回答提示。','Output: an answering prompt containing evidence.')],
      [t('根据证据写出回答','Write an answer from the evidence'),t('示意回答：“美国 REITs 始于 1960 年，见数据页号 4。”这里是人工流程示例，不是新增模型输出；本次实验评估的是页面排序。','Illustrative answer: “US REITs began in 1960; see dataset page ID 4.” This is a manually written pipeline example, not a new model output. Our experiment evaluates page ranking.'),t('输入：问题 + 找到的资料。','Input: the question + retrieved evidence.'),t('输出：带来源的自然语言回答。','Output: a natural-language answer with a source.')]
    ];
    const v=stages[state.ragStep];
    $$('[data-rag-step]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.ragStep)===state.ragStep)));
    $('#rag-detail').innerHTML=`<h3>${v[0]}</h3><p>${v[1]}</p><div class="grid-two"><div>${v[2]}</div><div>${v[3]}</div></div>`;
  }
  const scoreMatrices = [ [[.9,.2,.1],[.1,.8,.3],[.2,.1,.7]], [[.6,.7,.1],[.6,.5,.2],[.2,.1,.1]] ];
  function scoreLab() {
    return `<section class="lab-panel" id="score-lab"><div class="lab-label"><h3>${t('动手算：一个页面的 MaxSim','Calculate a page’s MaxSim')}</h3><span class="chip">${t('人工算例','Toy example')}</span></div><label for="score-page">${t('选择页面矩阵','Choose a page matrix')}</label> <select id="score-page"><option value="0" ${state.scorePage===0?'selected':''}>${t('页面 A','Page A')}</option><option value="1" ${state.scorePage===1?'selected':''}>${t('页面 B','Page B')}</option></select><div class="score-steps">${[t('① 看点积矩阵','① Dot products'),t('② 每行取最大','② Row maxima'),t('③ 加总得分','③ Sum the maxima')].map((name,i)=>`<button data-score-step="${i}" aria-pressed="${state.scoreStep===i}">${name}</button>`).join('')}</div><div id="score-matrix"></div><div id="score-result" class="formula" aria-live="polite"></div><p class="muted">${t('每个格子已经是一次点积的结果。q₁–q₃ 表示问题位置，d₁–d₃ 表示页面位置；数字是人为设计的示意，不是本题模型向量。','Each cell is an already computed dot product. q₁–q₃ are question positions; d₁–d₃ are page positions. These illustrative values are not the model vectors for our real question.')}</p></section>`;
  }
  // Synthetic dot-product matrices: row-wise best match, then sum (MaxSim).
  function showScore() {
    const matrix=scoreMatrices[state.scorePage], maxima=matrix.map(row=>Math.max(...row));
    $$('[data-score-step]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.scoreStep)===state.scoreStep)));
    $('#score-matrix').innerHTML=`<table class="score-matrix"><thead><tr><th>${t('问题 ↘ 页面','Question ↘ page')}</th><th>d₁</th><th>d₂</th><th>d₃</th><th>${t('行最大值','Row maximum')}</th></tr></thead><tbody>${matrix.map((row,i)=>`<tr><th>q${['₁','₂','₃'][i]}</th>${row.map(v=>`<td class="${state.scoreStep>=1&&v===maxima[i]?'chosen-max':''}">${v.toFixed(1)}</td>`).join('')}<td class="row-maximum">${state.scoreStep>=1?maxima[i].toFixed(1):'?'}</td></tr>`).join('')}</tbody></table>`;
    $('#score-result').textContent=state.scoreStep===0?t('先看矩阵：一行对应一个问题位置。','Read the matrix: one row per question position.'):state.scoreStep===1?t('每个问题位置，保留它在页面上最强的匹配。','Keep the strongest page match for each question position.'):`MaxSim = ${maxima.map(v=>v.toFixed(1)).join(' + ')} = ${maxima.reduce((a,b)=>a+b,0).toFixed(3)}`;
  }
  function kLab() {
    return `<section class="lab-panel" id="k-lab"><div class="lab-label"><h3>${t('调一调 K：候选数与相关页数','Adjust K: candidates versus relevant pages')}</h3><span class="chip">${t('人工算例','Toy example')}</span></div><p>${t('假设检索顺序固定为 A–G，数据集标签固定为 {C, E, F}。拖动滑块，观察哪些页进入重排。绿色表示标注相关；“入选 / 未入选”表示候选范围。','Suppose retrieval order is A–G and dataset gold is fixed at {C, E, F}. Move the slider to change the reranker’s candidate set. Green means labeled relevant; “selected / outside” marks membership.')}</p><label for="candidate-k">${t('送给重排模型的最多页数 K','Maximum pages passed to the reranker, K')}</label><div class="slider-row"><input id="candidate-k" type="range" min="1" max="7" value="${state.k}"><output id="k-value"></output></div><div id="k-pages" class="k-pages"></div><div id="k-result" class="formula" aria-live="polite"></div><p class="muted">${t('改变 K 不会改变 gold 标签。这个演示只计算候选覆盖率；真实重排和开销需要另外测量。','Changing K does not change gold labels. This demo calculates candidate coverage only; real reranking quality and cost require separate measurement.')}</p></section>`;
  }
  // K changes candidate membership only; the reference gold set stays fixed.
  function showK() {
    const pages=['A','B','C','D','E','F','G'],gold=new Set(['C','E','F']),found=pages.slice(0,state.k).filter(p=>gold.has(p)).length;
    $('#k-value').textContent=`K = ${state.k}`;
    $('#k-pages').innerHTML=pages.map((p,i)=>`<div class="k-page ${gold.has(p)?'gold':''} ${i<state.k?'selected':'outside'}" data-page="${p}" data-gold="${gold.has(p)}"><small>#${i+1}</small><strong>${p}</strong><span>${gold.has(p)?t('相关','Relevant'):t('负例','Negative')}</span><small>${i<state.k?t('入选','Selected'):t('未入选','Outside')}</small></div>`).join('');
    $('#k-result').textContent=`K = ${state.k} · ${t('召回相关页','Relevant pages retrieved')}: ${found}/3 = ${pct(found/3)} · ${t('读取图片','Images to read')}: ${state.k}`;
  }
  function loraLab() {
    return `<section class="lab-panel" id="lora-lab"><div class="lab-label"><h3>${t('动手算：LoRA 少训练了多少参数？','Calculate how many LoRA parameters are trained')}</h3><span class="chip">${t('单层人工算例','One-layer toy example')}</span></div><p>${t('延续上面的 1000×1000 矩阵，拖动 r 看看 A、B 的尺寸和参数量怎么变。r 增大，可表达的更新空间和训练参数量也随之增加。','Continue with the 1000×1000 matrix above. Move r to see the dimensions and parameter counts of A and B change. Raising r increases both the update’s capacity and the trainable parameter count.')}</p><label for="lora-rank">${t('低秩维度 r','Low-rank dimension r')}</label><div class="slider-row"><input id="lora-rank" type="range" min="1" max="64" value="${state.loraRank}"><output id="lora-rank-value"></output></div><div id="lora-count" class="formula" aria-live="polite"></div><p class="muted">${t('原始 W 仍要加载和参与计算；LoRA 不会把整个 7B 模型缩成这些参数。本实验实际跨多层共有 20,185,088 个可训练参数，不能直接用这个单层比例预测全部显存。','The original W is still loaded and used. LoRA does not shrink the entire 7B model to these parameters. Our run trains 20,185,088 parameters across many layers; this one-layer ratio does not predict total GPU memory.')}</p></section>`;
  }
  function showLora() {
    const r=state.loraRank;$('#lora-rank-value').textContent=`r = ${r}`;
    $('#lora-count').textContent=`${(2000*r).toLocaleString('en-US')} / 1,000,000 = ${(r/5).toFixed(1)}% · A: ${r}×1000 · B: 1000×${r}`;
  }
  function lessonNavigation() {
    const heads=$$('#lesson-root h2, #lesson-root > h3').filter(h=>!h.closest('details'));
    if(heads.length<3)return;
    heads.forEach((h,i)=>{h.id=h.id||`section-${current}-${i}`;});
    const nav=document.createElement('nav');nav.className='section-jumps';nav.setAttribute('aria-label',t('本节路线','In this lesson'));
    nav.innerHTML=`<span>${t('本节路线','In this lesson')}</span>${heads.map(h=>`<button data-section-jump="${h.id}">${esc(h.textContent)}</button>`).join('')}`;
    $('.chapter-title').after(nav);
  }
  function quizContent() {
    return {
      rag:[t('新增一份报告到 RAG 的资料库，会自动更新 Qwen 的参数吗？','Does adding a report to a RAG collection automatically update Qwen’s parameters?'),[t('会，检索就是训练','Yes: retrieval is training'),t('不会，索引和模型参数是两回事','No: the index and model parameters are separate'),t('只有选 5 页时会','Only when we choose five pages')],1,t('建立索引改变可找到的资料。SFT 等训练过程才通过损失与更新改变模型参数。','Indexing changes the available evidence. Training such as SFT changes parameters through loss and updates.')],
      data:[t('这道题的“相关页是数据页号 4”来自哪里？','Where does the label “dataset page ID 4 is relevant” come from?'),[t('ColQwen2 的最高分','ColQwen2’s highest score'),t('模型输出的 1960','The model’s answer, 1960'),t('数据集已有的 positive_passages','The dataset’s positive_passages')],2,t('数据集提供参考相关页；ColQwen2 提供相似度分数。二者独立，所以检索器才可能被评分、也可能排错。','The dataset supplies reference relevance; ColQwen2 supplies similarity scores. Their independence lets us evaluate retrieval and identify mistakes.')],
      retrieve:[t('K=5 表示什么？','What does K=5 mean?'),[t('最多给重排模型 5 张候选图','At most five candidate images go to the reranker'),t('每道题恰好有 5 个正确页','Every question has exactly five relevant pages'),t('模型认为正确概率是 5%','The model predicts a 5% probability')],0,t('K 是候选预算。它与 gold 数量独立；分数也不是概率。漏在候选之外的相关页，重排无法补回。','K is the candidate budget, independent of the number of gold pages. Scores are not probabilities. Reranking cannot recover evidence outside the candidate set.')],
      labels:[t('打乱图片后，哪些信息必须同步更新？','After shuffling images, what must be updated together?'),[t('只改图片位置','Only the image positions'),t('图片、页面对应、正确编号与目标排列','Images, page mapping, positive indices and target order'),t('让学生自己猜新标签','Let the student guess the new labels')],1,t('Label 指向同一证据页，但候选编号由输入位置决定。只换图不换编号，会把错误监督交给模型。','The label still refers to the same evidence page, but candidate indices depend on input position. Moving images without updating indices produces incorrect supervision.')],
      sft:[t('使用 LoRA 做本项目 SFT，实际更新什么？','With LoRA SFT in this project, what is updated?'),[t('所有页面的 gold 标签','Every page’s gold label'),t('ColQwen2 输出的分数','ColQwen2 retrieval scores'),t('重排模型选定层的 LoRA 参数','LoRA parameters in selected reranker layers')],2,t('SFT 是按示范学习的方法；LoRA 决定哪些参数参与更新。基础权重与视觉编码器冻结，检索器保持原状。','SFT learns from demonstrations; LoRA controls which parameters are updated. Base weights and the vision encoder are frozen, and the retriever stays unchanged.')],
      results:[t('固定同一组 5 张候选，只重排它们，Recall@5 会提高吗？','If the same five candidate pages are merely reordered, can Recall@5 increase?'),[t('不会，前 5 名包含的页面没变','No: the same pages remain in the first five'),t('会，只要相关页来到第一名','Yes: if a relevant page moves to rank one'),t('会，只要格式正确','Yes: if the format is valid')],0,t('相关页前移可以提高 Recall@1、MRR 等。固定集合的 Recall@5 不变；未召回的 gold 仍在分母里。','Moving relevant pages earlier can improve Recall@1 or MRR. Recall@5 is unchanged for a fixed set, and missed gold pages stay in the denominator.')],
      grpo:[t('同题一组输出奖励完全相同，这组的相对优势怎样？','If every output for one question has the same reward, what are their relative advantages?'),[t('全部是 +1','All are +1'),t('全部是 0','All are 0'),t('由检索分数决定','They depend on retrieval scores')],1,t('每个奖励减去组均值都是 0，因此没有组内奖励差异可学。其他正则项仍可能影响更新；高奖励也不能自动证明解释真实。','Every reward minus the group mean is zero, leaving no within-group reward difference. Other regularization can still affect updates, and high reward does not establish factual accuracy.')]
    }[current];
  }
  function showQuiz() {
    const q=quizContent();if(!q)return;
    let panel=$('#lesson-quiz');
    if(!panel){panel=document.createElement('section');panel.id='lesson-quiz';panel.className='lesson-quiz';$('#lesson-root').append(panel);}
    const selected=state.quizAnswers[current],answered=selected!==undefined,correct=selected===q[2];
    panel.innerHTML=`<span class="chip">${t('离开本节前，检查一下','Check your understanding')}</span><h3>${q[0]}</h3><div class="quiz-options">${q[1].map((a,i)=>`<button data-quiz-answer="${i}" data-correct="${i===q[2]}" aria-pressed="${selected===i}">${String.fromCharCode(65+i)}. ${a}</button>`).join('')}</div><p id="quiz-feedback" data-correct="${answered?correct:''}" class="quiz-feedback ${answered?(correct?'correct':'retry'):''}" aria-live="polite">${answered?`<strong>${correct?t('答对了。','Correct.'):t('再想一步。','Try again.')}</strong> ${q[3]}`:t('选一项，查看原因；可以反复尝试。','Choose an answer to see why. You can try again.')}</p>`;
  }

  function designFigure(name,caption,alt) {
    const path=`assets/${name}-${lang}.svg`;
    return `<figure class="teaching-diagram"><button data-image="${path}" data-caption="${esc(caption)}" aria-label="${t('放大流程图','Enlarge the diagram')}"><img src="${path}" alt="${esc(alt)}" loading="lazy"></button><figcaption>${caption} <a href="${path}" download>${t('下载图解','Download diagram')}</a></figcaption></figure>`;
  }
  function encodingComparison() {
    const h=H[lang];
    return `<section id="encoding-comparison">${h.encodingIntro}${designFigure('encoding-paths',h.encodingCaption,h.encodingAlt)}<div class="lab-panel design-selector"><h3>${t('选一条路线，看它保留什么、可能漏什么','Choose a route: what does it retain or miss?')}</h3><div class="design-tabs">${h.encodingMethods.map((m,i)=>`<button data-encoding-method="${i}" aria-pressed="${state.encodingMethod===i}">${m.name}</button>`).join('')}</div><div id="encoding-detail" aria-live="polite"></div></div>${h.encodingAfter}</section>`;
  }
  function showEncoding() {
    const m=H[lang].encodingMethods[state.encodingMethod];
    $$('[data-encoding-method]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.encodingMethod)===state.encodingMethod)));
    $('#encoding-detail').innerHTML=`<h3>${m.title}</h3><p>${m.body}</p><div class="design-example"><strong>${t('回到 REITs 问题','For our REITs question')}</strong><p>${m.example}</p></div><p class="design-tradeoff"><strong>${t('收益与代价：','Benefits and tradeoffs: ')}</strong>${m.tradeoff}</p>`;
  }
  // Supervision preparation uses real images; student SFT also keeps those images.
  function teacherPipeline() {
    const h=H[lang];
    const notes=t([
      '图 1：这是目录页。它列出 REITs 主题，但本题要问美国起始年份，该页没有给出。',
      '图 2：介绍印度 REITs 与市场数量。本题要问美国起始年份，该页没有直接给出。',
      '图 3：时间轴写 United States (1960)。本题问美国起始年份，这一页直接给出。',
      '图 4：介绍发行与上市规定。本题要问美国起始年份，该页没有给出。',
      '图 5：继续介绍发行与上市规定。本题要问美国起始年份，该页没有给出。'
    ],[
      'Image 1: an agenda lists REITs topics. The question asks for the US start year, which this page does not give.',
      'Image 2: REITs in India and market counts. The question asks for the US start year, which this page does not directly give.',
      'Image 3: the timeline says United States (1960). The question asks for the US start year, directly supported here.',
      'Image 4: issuing and listing rules. The question asks for the US start year, which this page does not give.',
      'Image 5: further issuing and listing rules. The question asks for the US start year, which this page does not give.'
    ]);
    return `<section id="teacher-pipeline">${h.teacherIntro}${designFigure('teacher-notes-flow',h.teacherCaption,h.teacherAlt)}<div class="lab-panel design-selector"><h3>${t('逐步看：输入、输出和目的','Step through the inputs, outputs and purpose')}</h3><div class="design-tabs">${h.teacherSteps.map((s,i)=>`<button data-teacher-step="${i}" aria-pressed="${state.teacherStep===i}">${s.name}</button>`).join('')}</div><div id="teacher-detail" aria-live="polite"></div></div><h2>${t('精炼前后，具体改变了什么？','What exactly changes during refinement?')}</h2><section id="refinement-comparison" class="refinement-comparison"><p class="muted">${t('以下是按真实页面内容撰写的人工教学简写，方便看出重复；不是新调用 Gemini 得到的输出，也不是原始逐页日志。','These are manual teaching abbreviations based on the real pages, written to expose repetition. They are not new Gemini outputs or the original per-page logs.')}</p><div class="grid-two"><article class="card"><span class="chip">${t('精炼前 · 逐页说明直接拼接','Before · concatenate the page notes')}</span><ol class="note-comparison">${notes.map(n=>`<li>${n}</li>`).join('')}</ol></article><article class="card refined-example"><span class="chip green">${t('精炼后 · 保留区别，删去重复','After · retain distinctions, remove repetition')}</span><p>${h.teacherSteps[1].example}</p><p class="takeaway">${t('保留：图 3、美国、1960，以及其他页为什么不够。删除：反复复述同一个问题。目标排列仍是 [3, 2, 1, 5, 4]。','Keep image 3, the US, 1960, and why the other pages are insufficient. Remove repeated restatements of the question. The target order remains [3, 2, 1, 5, 4].')}</p></article></div><p>${t('最终助手答案里的证据 token 与排列 token 都会计算 SFT 损失。解释越长，要模仿的文字位置越多；缩短目标不自动等于排序更好。','Both evidence tokens and permutation tokens receive SFT loss. Longer explanations add more text positions to imitate; shorter targets do not automatically yield better ranking.')}</p></section>${h.teacherAfter}</section>`;
  }
  function showTeacher() {
    const s=H[lang].teacherSteps[state.teacherStep];
    $$('[data-teacher-step]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.teacherStep)===state.teacherStep)));
    $('#teacher-detail').innerHTML=`<h3>${s.title}</h3><dl class="teacher-io"><div><dt>${t('输入','Input')}</dt><dd>${s.input}</dd></div><div><dt>${t('输出','Output')}</dt><dd>${s.output}</dd></div><div><dt>${t('为什么','Why')}</dt><dd>${s.reason}</dd></div></dl>${block(s.example,t('人工教学简写 · 固定使用保存的训练编号','Manual teaching abbreviation · saved training indices'))}`;
    enhanceCode();
  }

  function data() {
    const p=D.sftExample.pages.find(p=>p.isGold);
    return `<p class="chapter-intro">${t('我们要做的事：给一个问题，把最能帮助回答的页面排在前面。','Our task: given a question, put the most useful document pages first.')}</p>
    ${G[lang].dataIntro}
    <section id="data-example" class="data-example"><div><span class="chip green">MMDocIR · SlideVQA</span><h2>${t('美国 REITs 从哪一年开始？','In what year did REITS start in the US?')}</h2>${lang==='zh'?`<p class="muted" lang="en">${esc(rawExample.query)}</p>`:''}<dl class="data-fields"><div><dt>${t('问题','Question')}</dt><dd>${t('一条需要从文档中找证据的问题。','A question that needs evidence from a document.')}</dd></div><div><dt>${t('页面','Pages')}</dt><dd>${t('文档页面的图片，保留文字、表格和图。','Page images containing text, tables and figures.')}</dd></div><div><dt>Label</dt><dd>${t('数据集已标明哪些页相关。本题是数据页号 4。','The dataset identifies relevant pages. Here it labels page ID 4.')}</dd></div></dl><p class="takeaway">${t('<strong>Label 来自数据集已有的标注。</strong>它指向证据页；这一页直接写着 1960。','<strong>The relevance label is already in the dataset.</strong> It points to the evidence page, which explicitly says 1960.')}</p></div><figure><button data-image="${esc(p.imageOriginal)}" data-caption="${t('数据页号 4 · United States (1960)','Dataset page ID 4 · United States (1960)')}" aria-label="${t('放大原始证据页','Enlarge the original evidence page')}"><img src="${esc(p.image)}" alt="${t('REITs 全球发展时间轴，United States (1960)','REITs timeline showing United States (1960)')}"></button><figcaption>${t('真实数据中的证据页 · 点击放大','Evidence page from the real dataset · click to enlarge')}</figcaption></figure></section>
    ${details('raw-data',t('看原始标注长什么样','See the original annotation'),`${block(JSON.stringify(rawExample,null,2))}<p>${t('<code>positive_passages</code> 指定相关页，<code>negative_passages</code> 提供负例。这里的 4 是数据页号；后面模型使用的候选编号会另外分配。原记录没有给出标注者身份。','<code>positive_passages</code> identifies relevant pages; <code>negative_passages</code> supplies a negative example. The 4 here is a dataset page ID. Candidate image numbers are assigned later. The record does not identify the annotator.')}</p><p class="muted">SlideVQA_train.jsonl : 11571 · <a href="assets/sources/reits-original.json" download>${t('下载此条原始记录','Download this original record')}</a></p>`)}
    ${G[lang].dataAfter}
    <p class="next-idea">${t('接下来：页面很多，先让 ColQwen2 选出少量候选。','Next: use ColQwen2 to select a few candidate pages.')}</p>`;
  }
  function retrieve() {
    return `<p class="chapter-intro">${t('现在已经有问题与页面。先用 ColQwen2 计算每页的检索分数，选出少量候选，再交给重排模型。','We now have a question and page images. ColQwen2 scores the pages, selects a shortlist and passes it to the reranker.')}</p>
    ${flow([t('问题 + 页面','Question + pages'),'<strong>ColQwen2</strong>',t('候选图 + 检索顺序','Candidate images + retrieval order')])}
    <p>${t('本次数据已指定问题对应的文档，因此在该文档的可用页面中计算分数。更大的 RAG 系统也可以先跨文档搜索；理解这一步，只需跟着“问题 → 页面分数 → 候选”走。','Here the dataset specifies the document for each question, so we score its available pages. A larger RAG system may search across documents. For this step, follow question → page scores → candidates.')}</p>
    ${G[lang].retrievalIntro}${encodingComparison()}${G[lang].scoreIntro}${scoreLab()}${G[lang].scoreAfter}
    ${block('similarities = Q @ D.T\nbest_match_per_query_token = similarities.max(axis=1)\nscore = best_match_per_query_token.sum()',t('把本地 MaxSim 代码拆成三行，便于逐步理解；完整源码见本节末尾。','The local MaxSim operation, expanded into three teaching lines. Full source is available below.'))}
    ${G[lang].kIntro}${kLab()}${G[lang].kAfter}<div id="candidate-gallery">${gallery(retrievedPages(),true,true)}</div>
    <p class="takeaway">${t('接下来由 <strong>Qwen2.5-VL-7B 重排模型</strong>细看这些图，再生成新顺序。<strong>SFT 是训练这个重排模型的方法。</strong>本项目不通过 SFT 更新 ColQwen2。','Next, the <strong>Qwen2.5-VL-7B reranker</strong> examines these images and generates an order. <strong>SFT trains this reranker.</strong> This project does not update ColQwen2 through SFT.')}</p>${snippet('retrieval')}
    <p class="next-idea">${t('接下来：分清 label、分数与目标答案，构造训练示范。','Next: distinguish labels, scores and target answers, then build a training example.')}</p>`;
  }
  function labels() {
    return `<p class="chapter-intro">${t('模型要学两部分：简短的页面证据说明，以及所有候选图的完整排列。它们的来源不同。','The model learns two parts: a short page-evidence summary and a complete ordering of the candidate images. These have different sources.')}</p>
    ${G[lang].labelsAfter}
    <div class="lab-panel compact-panel"><div class="lab-label"><h3>${t('还是这道 REITs 题','The same REITs question')}</h3><button id="shuffle-toggle" class="quiet-button"></button></div><p id="shuffle-caption" class="muted"></p><div id="label-gallery"></div><div id="supervision-preview"></div></div>
    ${teacherPipeline()}
    ${details('supervision-details',t('展开真实训练答案与检查方法','Expand the saved target and checks'),`${block(D.sftExample.messages.at(-1).content)}<p>${t('以上原文始终对应打乱后的训练输入；上方切换只演示编号映射。<code>&lt;think&gt;</code> 存放公开证据摘要；<code>&lt;answer&gt;</code> 存放全部候选编号。','This saved target always refers to the shuffled training input; the toggle above only illustrates index mapping. <code>&lt;think&gt;</code> contains a public evidence summary; <code>&lt;answer&gt;</code> contains every candidate index.')}</p><p>${t('自动检查编号完整、无重复、图片对应；人工核对年份、表格列和解释事实。教师也会读错。训练构造可以补入相关页，正式测试不能补。教师可见标签，学生的输入不包含标签或检索分数。','Check that indices are complete, unique and bound to the right images; inspect dates, table columns and factual claims manually. Teachers can misread pages. Training construction may insert relevant pages; testing must not. The teacher can see labels, but the student prompt contains neither labels nor retrieval scores.')}</p>`)}${snippet('annotation')}
    <p class="next-idea">${t('接下来：用“问题 + 图片 → 证据 + 排列”这样的示范做 SFT。','Next: use these question-and-images → evidence-and-order examples for SFT.')}</p>`;
  }
  function sft() {
    const lossPanel = details('loss-lab',t('动一下：正确答案概率与损失','Try it: target probability and loss'),`<p>${t('只对助手的证据和排列计算训练损失。问题与图片作为输入，不作为要预测的答案。','The evidence and ordering in the assistant completion receive training loss. The question and images provide the input, not the prediction target.')}</p><div class="token-row"><span class="token">${t('问题 + 图片：输入','Question + images: input')}</span><span class="token target">${t('证据 + 排列：目标','Evidence + order: target')}</span></div><label for="token-probability">${t('某个正确目标 token 的预测概率','Predicted probability of one correct target token')}</label><div class="slider-row"><input id="token-probability" type="range" min="1" max="99" value="${state.probability}"><output id="token-probability-value"></output></div><div id="loss-value" class="formula" aria-live="polite"></div><p class="muted">${t('人工单 token 交叉熵演示，不是训练日志。真实训练对多个有效目标 token 汇总。','Illustrative single-token cross-entropy, not a training log. Training aggregates loss across the unmasked target tokens.')}</p>`).replace('<details ', '<details open ');
    return L[lang].sft.replace('<h3>4 ·',lossPanel+'<h3>4 ·').replace('<h3>5 ·',loraLab()+'<h3>5 ·') + E[lang].merge + snippet('sftTrain');
  }
  function scoreCards() {
    const rows=['base','sft','grpo'].map(id=>D.metrics.rows.find(r=>r.id===id));
    return `<div class="score-cards">${rows.map(r=>`<article class="card score-card ${r.id}"><span>${modelName(r.id)}</span><strong>${pct(r.recall1)}</strong><small>Macro Recall@1</small></article>`).join('')}</div>`;
  }
  function grpoScoreCards() {
    const seen=D.grpoExperiment.selectedExposure;
    return `<div class="score-cards exposure-cards">${[[seen.fresh_groups,t('新采样组','fresh sampling groups')],[seen.fresh_completions,t('新生成输出','fresh generated outputs')],[seen.ranking_signal_groups,t('有排序奖励差异的组','groups with ranking reward differences')]].map(([value,name])=>`<article class="card"><strong>${Number(value).toLocaleString('en-US')}</strong><span>${name}</span></article>`).join('')}</div>`;
  }
  function results() {
    const before=D.metrics.paired.sftMinusBase.recall1, after=D.metrics.paired.grpoMinusSft.recall1;
    return `<p class="chapter-intro">${t('同一 1,658 道测试题，同一候选图片：横向比较 Base、合并 SFT900 与继续训练后的 GRPO512。','The same 1,658 test questions and candidate images: compare Base, merged SFT900 and the continued-training GRPO512 model.')}</p>
    ${G[lang].resultsIntro}${scoreCards()}
    <div class="result-summary"><div><strong>+${(before.delta*100).toFixed(2)}</strong><span>SFT900 merged − Base · ${t('百分点','percentage points')}</span></div><div><strong>+${(after.delta*100).toFixed(2)}</strong><span>GRPO512 − SFT900 merged · ${t('百分点','percentage points')}</span></div></div>
    <label for="metric-select">${t('切换排序指标','Choose a ranking metric')}</label> <select id="metric-select">${[['recall1','Macro Recall@1'],['recall3','Macro Recall@3'],['recall5','Macro Recall@5'],['mrr','MRR'],['ndcg5','nDCG@5']].map(([key,name])=>`<option value="${key}" ${state.metric===key?'selected':''}>${name}</option>`).join('')}</select><div id="metric-chart" class="metric-bars"></div><p class="muted" id="metric-description"></p>
    <p class="muted">${t('ColQwen2 检索原序作为同一批候选的参考起点。Base、SFT、GRPO 均对这些页面重排；本项目没有通过 SFT 或 GRPO 更新 ColQwen2。','ColQwen2 retrieval order is the reference starting point for these candidates. Base, SFT and GRPO rerank the same pages; neither training stage updates ColQwen2.')}</p>
    <h2>${t('格式与排序，要分别看','Read format validity and ranking separately')}</h2><table><thead><tr><th>${t('模型','Model')}</th><th>${t('格式失败并回退的题数','Queries with format fallback')}</th><th>MRR</th><th>nDCG@5</th></tr></thead><tbody>${D.metrics.rows.filter(r=>r.id!=='retrieval').map(r=>`<tr><td>${modelName(r.id)}</td><td>${r.fallback.toLocaleString('en-US')} / 1,658</td><td>${num(r.mrr)}</td><td>${num(r.ndcg5)}</td></tr>`).join('')}</tbody></table>
    <p class="takeaway">${t('Base 的 <strong>1,607 / 1,658</strong> 份输出不符合完整排序协议，使用检索原序计分。SFT900 merged 与 GRPO512 的格式回退均为 <strong>0</strong>。SFT 的提高包含格式学习；GRPO 相对 SFT 的变化来自有效排列的改变，仍需核对解释事实。','Base has <strong>1,607 / 1,658</strong> outputs failing the complete-ranking protocol, scored using retrieval fallback. SFT900 merged and GRPO512 both have <strong>zero</strong> format fallbacks. The SFT gain includes format learning; the GRPO-versus-SFT difference comes from valid ranking changes. Evidence factuality still requires checking.')}</p>
    ${G[lang].resultsAfter}
    ${details('replay-details',t('展开真实案例：三模型逐题对比','Explore real cases: compare all three models'),`<div class="case-controls"><label for="case-select">${t('案例','Case')}</label><select id="case-select">${D.cases.map((c,i)=>`<option value="${i}" ${state.caseIndex===i?'selected':''}>${esc(caseInfo(c).title)}</option>`).join('')}</select><button id="toggle-gold"></button></div><div id="case-body"></div>`)}
    ${details('metrics-lab',t('手算：为什么漏检一页，分母仍是 2？','Work it out: why does a missed page stay in the denominator?'),`<p>${t('候选为 A–E，全部相关页为 {B, F}。F 没有召回。移动 B，观察指标：','Candidates are A–E; all relevant pages are {B, F}. F was not retrieved. Move B and watch the metrics:')}</p><div id="sort-list" class="sort-list"></div><div id="sort-metrics" class="metric-readout" aria-live="polite"></div><button id="sort-reset">${t('重置顺序','Reset order')}</button><p class="muted">${t('人工算例。Recall@K = 前 K 名命中的相关页数 / 全部相关页数；Hit@1 只看第一名是否命中；MRR 对第一张相关页的名次取倒数再求平均。','Illustrative example. Recall@K = relevant pages in the first K positions / all relevant pages. Hit@1 checks whether the first page is relevant. MRR averages the reciprocal rank of the first relevant page.')}</p>`)}
    ${details('evaluation-notes',t('同一测试集的评估口径与来源','Same-test-set protocol and sources'),`<p>${t('固定 1,658 题；候选、提示、生成设置和完整 gold 分母不变。严格格式失败时保留原检索顺序，失败题仍计入总分。五张候选仅改变位置，因此 Recall@5 不变。','Fix all 1,658 questions, candidates, prompts, decoding settings and full gold denominators. Invalid outputs keep retrieval order and remain in the score. Reordering five fixed candidates leaves Recall@5 unchanged.')}</p><p>${t('GRPO512 − SFT900 merged 的 Macro Recall@1 配对 95% 区间为 +0.96 至 +2.82 个百分点。每次把同一道题的两模型分数一起重采样，共 1,000 次，seed=42。该区间不包含重新训练的随机种子方差，也未按文档聚类。','The paired 95% interval for GRPO512 − SFT900 merged Macro Recall@1 is +0.96 to +2.82 percentage points. The two models’ scores for each query are resampled together 1,000 times with seed 42. This excludes retraining-seed variation and does not cluster by document.')}</p><p><a href="assets/sources/current-test-report.md.txt" target="_blank">${t('三模型测试报告','Three-model test report')}</a> · <a href="assets/sources/metrics-current.json" download>${t('下载完整指标','Download full metrics')}</a></p>`)}
    <p class="next-idea">${t('接下来：解释 GRPO 为什么能从同题多份输出的奖励差异中学习，以及这次训练实际做了什么。','Next: understand how GRPO learns from reward differences within a question, and what our completed run actually did.')}</p>`;
  }
  const rewardChoices = [[2,4,1,3,5],[2,1,4,3,5],[1,3,5,2,4],[2,2,4,3,5]];
  function rewardOptions(selected) { return rewardChoices.map((v,i)=>`<option value="${i}" ${i===selected?'selected':''}>${esc(JSON.stringify(v))}${i===3?t(' · 非法排列',' · invalid'):''}</option>`).join(''); }
  function grpo() {
    return L[lang].grpo + details('reward-lab',t('手算一组奖励与相对优势','Calculate rewards and relative advantages'),`<p>${t('假设 5 张候选中 {2, 4} 相关。同一道题生成两份输出，比较奖励：','Suppose images {2, 4} are relevant among five candidates. Compare two outputs for the same question:')}</p><div class="reward-controls"><div><label for="reward-a">${t('输出 A','Output A')}</label><select id="reward-a">${rewardOptions(state.rewardA)}</select></div><div><label for="reward-b">${t('输出 B','Output B')}</label><select id="reward-b">${rewardOptions(state.rewardB)}</select></div></div><div id="reward-result" aria-live="polite"></div><p class="muted">${t('人工示意：合法格式奖励 1，加上相关页名次的 1/r³ 得分，并用理想顺序归一化；非法排列两项均为 0。优势用组内总体标准差 + 10⁻⁸ 归一化。用于理解机制；真实四输出配置与日志统计见下方。','Illustration: valid format earns 1, plus a ranking score summing 1/r³ for relevant-page ranks and normalized by the ideal order. Invalid permutations earn zero for both. Advantages use the population group standard deviation + 10⁻⁸. The real four-output run and log counts appear below.')}</p>`).replace('<details ', '<details open ') + E[lang].grpo + E[lang].signal + grpoScoreCards() + E[lang].result;
  }
  function resources() {
    const docs=[['current-test-report.md.txt','三模型同一测试集报告','Three-model report on the same test set']];
    return `<h2>${t('源码与参考资料','Source code and references')}</h2>${SITE.codeDownload&&/^https?:$/.test(location.protocol)?`<p><a href="${esc(SITE.codeDownload)}" download>${t('下载当前项目代码','Download current project code')}</a> · ${esc((SITE.updatedAt||'').slice(0,10))}</p>`:''}${SITE.githubUrl?`<p><a href="${esc(SITE.githubUrl)}" target="_blank" rel="noopener noreferrer">GitHub · ${t('最新项目代码','latest project code')}</a></p>`:''}<p class="muted">${t('下方代码片段对应课堂中的已保存实验，附有解释。它们说明每一步的输入、输出和作用。','The explained snippets below match the saved classroom experiment and identify each step’s inputs, outputs and purpose.')}</p><label for="source-select">${t('选择代码','Choose a source file')}</label> <select id="source-select">${Object.keys(D.code).map(id=>`<option value="${id}">${esc(codeInfo(id).title)}</option>`).join('')}</select><div id="source-view"></div>
    ${details('reference-files',t('项目报告与来源记录','Project reports and provenance'),`<p class="muted">${t('这里提供本项目的数据、训练与评估资料；中英文课堂使用同一份数据和结果。','These project files document data, training and evaluation. Both classroom languages use the same data and results.')}</p><div class="resource-list">${docs.map(([id,zh,en])=>`<a href="assets/sources/${id}" target="_blank" class="resource-item">${t(zh,en)}${lang==='en'?' · original Chinese':''}</a>`).join('')}<a href="assets/provenance.json" target="_blank" class="resource-item">${t('数据与源码来源清单','Dataset and source provenance')}</a></div>`)}
    ${details('papers',t('官方资料与论文','Official resources and papers'),`<ul><li><a href="https://arxiv.org/abs/2005.11401" target="_blank" rel="noreferrer">RAG · Lewis et al., 2020</a></li><li><a href="https://huggingface.co/datasets/MMDocIR/MMDocIR_Evaluation_Dataset" target="_blank" rel="noreferrer">MMDocIR</a></li><li><a href="https://huggingface.co/vidore/colqwen2-v1.0-hf" target="_blank" rel="noreferrer">ColQwen2</a></li><li><a href="https://arxiv.org/abs/2106.09685" target="_blank" rel="noreferrer">LoRA · Hu et al., 2021</a></li><li><a href="https://arxiv.org/abs/2402.03300" target="_blank" rel="noreferrer">GRPO · DeepSeekMath, 2024</a></li></ul><p class="muted">${t('以上外部链接需要网络；网站本身可离线使用。','These external links require a connection. The classroom itself works offline.')}</p>`)} `;
  }
  const renderers = {rag,data,retrieve,labels,sft,results,grpo,lab:()=>L[lang].lab+resources()};
  function shell() {
    document.documentElement.lang=lang==='zh'?'zh-CN':'en';
    $('.skip-link').textContent=t('跳到课堂内容','Skip to lesson');
    $('#brand-caption').textContent=t('一道题，走完整个项目','One question, the whole project');
    $('#nav-caption').textContent=t('按顺序，逐步理解','Follow the steps');
    $('#sidebar-note').textContent=t('跟着数据，逐步往前。','Follow the data, step by step.');
    $('#course-caption').textContent=t('交互课堂','Classroom');
    $('#language-toggle').textContent=t('English','中文');
    $('#language-toggle').lang=t('en','zh-CN');
    $('#language-toggle').setAttribute('aria-label',t('Switch to English','切换为中文'));
    $('#glossary-open').setAttribute('aria-label',t('术语速查','Glossary'));
    $('#glossary-open').innerHTML=t('术语速查 <kbd>/</kbd>','Glossary <kbd>/</kbd>');
    $('#present-toggle').textContent=document.body.classList.contains('presentation')?t('退出投影','Exit presentation'):t('投影模式','Present');
    $('#previous').textContent=t('← 上一节','← Previous');
    $('#reset-progress').textContent=t('重置学习标记','Reset progress');
    $('#snapshot-caption').textContent=t('真实页面 · 已保存的实验结果','Real pages · saved experiment results');
    $('#share-link').textContent=t('分享','Share');
    $('#share-link').setAttribute('aria-label',t('分享当前章节链接','Share this lesson link'));
    const repository=$('#github-link');repository.hidden=!SITE.githubUrl;
    if(SITE.githubUrl){repository.href=SITE.githubUrl;repository.setAttribute('aria-label',t('GitHub · 最新项目代码','GitHub · latest project code'));}
    $('#release-caption').textContent=SITE.updatedAt?t('更新于 ','Updated ')+SITE.updatedAt.slice(0,10):'';
    $('#update-caption').textContent=t('课堂有更新。','A new classroom version is available.');
    $('#load-latest').textContent=t('加载最新版','Load latest');
    $('#shortcut-caption').textContent=t('方向键翻页 · / 术语 · P 投影','Arrow keys: lessons · /: glossary · P: present');
    $('#glossary-title').textContent=t('随时查一个词。','Look up a term.');
    $('#glossary-search').placeholder=t('搜索 SFT、标签、损失…','Search SFT, labels, loss…');
    $('label[for="glossary-search"]').textContent=t('搜索术语','Search terms');
    $('#sidebar').setAttribute('aria-label',t('课程导航','Course navigation'));
    $('#chapter-nav').setAttribute('aria-label',t('章节','Lessons'));
    $('#menu-toggle').setAttribute('aria-label',t('展开课程目录','Open course menu'));
    $('[data-close="glossary-dialog"]').setAttribute('aria-label',t('关闭术语速查','Close glossary'));
    $('[data-close="image-dialog"]').setAttribute('aria-label',t('关闭页面大图','Close enlarged page'));
  }
  function progress() {
    $('#progress-label').textContent=`${t('学习进度','Progress')} · ${completed.size} / ${chapters.length}`;
    $('#progress-fill').style.width=`${completed.size/chapters.length*100}%`;
    $('#mark-done').textContent=completed.has(current)?t('✓ 已理解本节','✓ Understood'):t('标记已理解','Mark as understood');
    $('#mark-done').setAttribute('aria-pressed',String(completed.has(current)));
  }
  // Replace only the current lesson, then rebind its arithmetic/rendered controls.
  function render(id, options={}) {
    $('#toast').textContent='';$('#toast').classList.remove('visible');
    id=aliases[id]||id;
    const c=chapters.find(c=>c[0]===id)||chapters[0];current=c[0];shell();
    $('#lesson-root').innerHTML=`<header class="chapter-title"><div class="chapter-meta"><span class="chip green">${current==='lab'?t('实践附录','PRACTICE'):t('第 '+(chapters.indexOf(c)+1)+' 步','STEP '+(chapters.indexOf(c)+1))}</span><span class="chip">${t('同一道题，从前往后','One example, step by step')}</span></div><h1>${t(c[3],c[4])}</h1></header>${renderers[current]()}`;
    $('#chapter-nav').innerHTML=chapters.map((n,i)=>`<a href="#${n[0]}" class="nav-item ${n[0]===current?'active':''} ${completed.has(n[0])?'done':''}" ${n[0]===current?'aria-current="page"':''}><span class="nav-number">${n[0]==='lab'?'+':String(i+1).padStart(2,'0')}</span><span>${label(n)}</span></a>`).join('');
    $('#crumb').textContent=label(c);document.title=`${label(c)} · DocReranker`;
    const i=chapters.indexOf(c);$('#previous').disabled=i===0;$('#next').disabled=i===chapters.length-1;
    $('#next').textContent=i===chapters.length-1?t('已到最后','End of course'):`${t('下一步','Next')}: ${label(chapters[i+1])} →`;
    $('#sidebar').classList.remove('open');$('#menu-toggle').setAttribute('aria-expanded','false');
    if(current==='labels'){showSupervision();showTeacher();}
    if(current==='rag')showRag();
    if(current==='retrieve'){showEncoding();showScore();showK();}
    if(current==='sft'){showLoss();showLora();}
    if(current==='results'){showMetric();showCase();showSort();}
    if(current==='grpo')showReward();
    if(current==='lab'){$('#source-select').value=state.source;showSource(state.source);}
    $$('#lesson-root details:not([id])').forEach((node,index)=>{node.id=`fold-${current}-${index}`;});
    if(options.panels)Object.entries(options.panels).forEach(([id,open])=>{const d=document.getElementById(id);if(d?.tagName==='DETAILS')d.open=open;});
    lessonNavigation();showQuiz();enhanceCode();progress();
    if(options.scroll!==undefined)window.scrollTo({top:options.scroll,behavior:'instant'});else window.scrollTo({top:0,behavior:'instant'});
  }
  function navigate(id) { if(location.hash===`#${id}`)render(id);else location.hash=id; }
  // Preserve both open and intentionally closed details, exercise state and scroll.
  function switchLanguage() {
    const panels=Object.fromEntries($$('#lesson-root details').map(d=>[d.id,d.open])), scroll=window.scrollY;
    lang=lang==='zh'?'en':'zh';save('docreranker-language',lang);
    $('#toast').textContent='';$('#toast').classList.remove('visible');
    $('#image-caption').textContent='';$('#image-full').alt='';
    const url=new URL(location.href);url.searchParams.set('lang',lang);
    try { history.replaceState(null,'',url); } catch (_) { /* Storage still remembers the choice. */ }
    render(current,{panels,scroll});showGlossary($('#glossary-search').value);
  }
  function showSupervision() {
    const pages=state.shuffled?D.sftExample.pages:retrievedPages();
    const gold=pages.filter(p=>p.isGold).map(p=>p.index);
    const order=state.shuffled?D.sftExample.targetRanking:[1,2,3,4,5];
    $('#shuffle-toggle').textContent=state.shuffled?t('对照原检索顺序','Compare retrieval order'):t('查看训练时的打乱顺序','Show shuffled training order');
    $('#shuffle-toggle').setAttribute('aria-pressed',String(state.shuffled));
    $('#shuffle-caption').textContent=state.shuffled?t('训练前先打乱候选；下方是实际保存的输入顺序。编号从 1 重新分配。','Candidates are shuffled before training. Below is the saved input order, with image numbers reassigned from 1.'):t('下方回到原检索顺序，编号随输入重新分配；这是对应关系演示。','Back in retrieval order, image numbers follow the new input positions. This view illustrates the mapping.');
    $('#label-gallery').innerHTML=gallery(pages,true);
    $('#supervision-preview').innerHTML=`<div class="supervision-mapping"><div><span>${t('相关图编号','Relevant image index')}</span><code>${JSON.stringify(gold)}</code></div><span aria-hidden="true">→</span><div><span>${t('代码构造的目标排列','Code-built target order')}</span><code>${JSON.stringify(order)}</code></div></div><p class="muted">${t('同一证据页的身份没变，只是“坐到了不同位置”。相关页始终排在目标最前面。','The evidence page is the same; only its input position changes. It stays first in the target order.')}</p>`;
  }
  function showLoss() {
    if(!$('#loss-value'))return;const p=state.probability/100;
    $('#token-probability-value').textContent=pct(p);
    $('#loss-value').textContent=`${t('单 token 损失','Single-token loss')} = −ln(${p.toFixed(2)}) = ${(-Math.log(p)).toFixed(3)}`;
  }
  function showMetric() {
    const key=state.metric;
    $('#metric-chart').innerHTML=D.metrics.rows.map(r=>`<div class="bar-row"><span>${modelName(r.id)}</span><div class="bar-track"><div class="bar-fill" style="width:${r[key]*100}%"></div></div><span class="bar-value">${key.startsWith('recall')?pct(r[key]):num(r[key])}</span></div>`).join('');
    $('#metric-description').textContent=({recall1:t('Recall@1：第一名找回了该题全部相关页中的多大比例，再对所有题取平均。','Recall@1: the fraction of all relevant pages found in first place, averaged over queries.'),recall3:t('Recall@3：前 3 名找回的相关页比例，再对所有题取平均。','Recall@3: the fraction of relevant pages found in the first 3 positions, averaged over queries.'),recall5:t('同一批候选只改变顺序，Recall@5 不会变。','Reordering the same candidate set leaves Recall@5 unchanged.'),mrr:t('MRR：第一张相关页的名次取倒数，再对题目求平均。','MRR: the average reciprocal rank of the first relevant page.'),ndcg5:t('nDCG@5：相关页越靠前得分越高，再与理想排序归一化。','nDCG@5: give more credit to relevant pages near the top, then normalize by the ideal order.')})[key];
  }
  function caseInfo(c) { return lang==='en'?{...c,...I.en.cases[c.id]}:c; }
  function reviewHtml(review) {
    if(!review)return '';const list=values=>`<ul>${values.map(v=>`<li>${esc(v)}</li>`).join('')}</ul>`;
    return `<p>${esc(review.summary)}</p>${review.notes?.length?list(review.notes):''}${review.rankingFinding?`<p>${esc(review.rankingFinding)}</p>`:''}${[['baseFindings',t('Base 说明核查','Base explanation review')],['sftFindings',t('SFT 说明核查','SFT explanation review')],['grpoFindings',t('GRPO 说明核查','GRPO explanation review')]].map(([k,label])=>review[k]?.length?`<h4>${label}</h4>${list(review[k])}`:'').join('')}<p class="muted">${esc(review.scope||'')}</p>`;
  }
  function showCase() {
    const c=D.cases[state.caseIndex],localized=caseInfo(c),v=c.variants[state.variant];
    $('#toggle-gold').textContent=state.gold?t('隐藏标签','Hide labels'):t('显示标签','Show labels');
    $('#toggle-gold').setAttribute('aria-pressed',String(state.gold));
    $('#case-body').innerHTML=`<div class="case-question"><blockquote lang="en">${esc(c.query)}</blockquote>${lang==='zh'?`<p>${esc(c.queryZh)}</p>`:''}</div>${gallery(c.pages,state.gold)}<div class="tabs" aria-label="${t('选择模型','Choose a model')}">${['retrieval','base','sft','grpo'].map(id=>`<button data-variant="${id}" aria-pressed="${id===state.variant}">${modelName(id)}</button>`).join('')}</div><span class="chip ${v.fallback?'orange':'green'}">${state.variant==='retrieval'?t('检索原序','Retrieval order'):v.fallback?t('格式失败，回退原序','Invalid format; retrieval fallback'):t('格式有效','Valid format')}</span>${strip(v.ranking)}${state.gold?`<p class="mini-metrics">Recall@1: <strong>${pct(v.metrics['recall@1'])}</strong> · MRR: <strong>${num(v.metrics.mrr)}</strong></p><p class="muted">${t('此题全部标注相关页','All labeled relevant pages for this query')}: ${c.goldPageIds.length} · ${t('其中进入候选的页数','Of these, pages in the candidates')}: ${c.goldCandidateIndices.length}</p>`:''}${state.variant==='retrieval'?'':details('case-output',t('模型输出原文（英文）','Original model output (English)'),block(v.rawOutput))}<p>${esc(localized.lesson)}</p>${details('case-review',t('人工核查与标签问题','Human review and label issues'),reviewHtml(localized.review))}<p class="muted">${t('选例规则','Selection')}: ${esc(localized.selectionReason)}. ${t('少量案例只用于讲解。','Selected cases are for illustration only.')}</p>`;
    enhanceCode();
  }
  function showSort() {
    const rank=state.order.indexOf('B')+1;
    $('#sort-list').innerHTML=state.order.map((v,i)=>`<div class="sort-item ${v==='B'?'gold':''}"><small>${t('第 '+(i+1)+' 名','Rank '+(i+1))}</small><b>${v}</b><small>${v==='B'?t('相关','Relevant'):t('不相关','Irrelevant')}</small><button data-move="${i}" data-direction="-1" aria-label="${t('将 '+v+' 前移','Move '+v+' earlier')}" ${i===0?'disabled':''}>←</button> <button data-move="${i}" data-direction="1" aria-label="${t('将 '+v+' 后移','Move '+v+' later')}" ${i===4?'disabled':''}>→</button></div>`).join('');
    $('#sort-metrics').innerHTML=[['Recall@1',rank===1?'1/2 = 0.5':'0/2 = 0'],['Hit@1',rank===1?'1':'0'],['Recall@5','1/2 = 0.5'],['MRR',`1/${rank} = ${(1/rank).toFixed(2)}`]].map(([k,v])=>`<div>${k}<strong>${v}</strong></div>`).join('');
  }
  // Teaching reward: valid format + ideal-normalized cubic rank discount.
  // It evaluates permutation structure/relevance, never the factuality of prose.
  function reward(index) {
    const order=rewardChoices[index];if(new Set(order).size!==5)return 0;
    return 1+[2,4].reduce((s,id)=>s+1/(order.indexOf(id)+1)**3,0)/(1+1/8);
  }
  // Two illustrative generations share one query; use population SD for the group.
  function showReward() {
    const values=[reward(state.rewardA),reward(state.rewardB)],mean=(values[0]+values[1])/2;
    const sd=Math.sqrt(values.reduce((s,v)=>s+(v-mean)**2,0)/2);
    $('#reward-result').innerHTML=`<table><thead><tr><th>${t('输出','Output')}</th><th>${t('格式','Format')}</th><th>${t('排序','Ranking')}</th><th>${t('总奖励','Total reward')}</th><th>${t('相对优势','Relative advantage')}</th></tr></thead><tbody>${values.map((r,i)=>`<tr><td>${i?'B':'A'}</td><td>${r>0?'1':'0'}</td><td>${(r>0?r-1:0).toFixed(5)}</td><td>${r.toFixed(5)}</td><td>${((r-mean)/(sd+1e-8)).toFixed(3)}</td></tr>`).join('')}</tbody></table><div class="formula">${t('均值','Mean')}: ${mean.toFixed(5)} · ${t('总体标准差','Population SD')}: ${sd.toFixed(5)}</div><p>${sd===0?t('两份输出同分，相对优势均为 0，没有组内奖励差异可学。','Both outputs have equal rewards. Their relative advantages are zero, so there is no within-group reward difference to learn from.'):t('奖励项倾向于提高正优势输出的概率，降低负优势输出的概率；实际更新还受裁剪、KL 等影响。','The reward term favors positive-advantage outputs over negative-advantage ones. Clipping and KL terms can also affect the actual update.')}</p>`;
  }
  function showSource(id) {
    const c=codeInfo(id);$('#source-view').innerHTML=details('selected-source',`${esc(c.title)} · ${t('展开代码','expand code')}`,`<p>${esc(c.explanation)}</p>${block(c.code,`${esc(c.path)} : ${c.startLine}–${c.endLine} · <a href="${esc(c.download)}" download>${t('下载完整源码','Download full source')}</a>`)}`);enhanceCode();
  }
  function enhanceCode() {
    $$('.code-block').forEach(el=>{if($('.copy-code',el))return;const b=document.createElement('button');b.className='copy-code';b.textContent=t('复制','Copy');b.setAttribute('aria-label',t('复制此代码块','Copy this code block'));b.addEventListener('click',()=>copy($('pre',el).textContent,b));el.prepend(b);});
  }
  // Share the current language/chapter. A local file path is not a public URL.
  async function shareLesson() {
    const base=SITE.publicUrl || (/^https?:$/.test(location.protocol)?location.href:null);
    if(!base){toast(t('请从公开网站分享链接。','Open the published site to share its link.'));return;}
    const url=new URL(base);url.searchParams.set('lang',lang);url.hash=current;
    if(navigator.share){try{await navigator.share({title:document.title,url:url.href});return;}catch(error){if(error.name==='AbortError')return;}}
    await copy(url.href,$('#share-link'));
  }
  // Existing tabs can discover a deployment without resetting a student's work.
  // The new version is applied only when the student clicks the reload button.
  async function checkVersion() {
    if(!/^https?:$/.test(location.protocol)||document.hidden)return;
    try{const response=await fetch('version.json',{cache:'no-store'});if(!response.ok)return;
      const release=await response.json();if(SITE.version&&release.version!==SITE.version)$('#update-notice').hidden=false;
    }catch(_){/* Offline reading continues if the network is temporarily unavailable. */}
  }
  if(/^https?:$/.test(location.protocol))setInterval(checkVersion,60000);
  let toastTimer;
  function toast(text) { $('#toast').textContent=text;$('#toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),2300); }
  async function copy(text, button) {
    let success=false;try{await navigator.clipboard.writeText(text);success=true;}catch(_){const area=document.createElement('textarea');area.value=text;area.style.cssText='position:fixed;left:-9999px';document.body.append(area);area.select();try{success=document.execCommand('copy');}catch(_){}area.remove();button.focus();}
    toast(success?t('已复制','Copied'):t('请选中代码手动复制','Select the code and copy it manually'));
  }
  function showGlossary(query='') {
    const matches=I[lang].glossary.filter(v=>[v.term,v.en,v.definition,v.example].join(' ').toLowerCase().includes(query.toLowerCase()));
    $('#glossary-results').innerHTML=matches.length?matches.map(v=>`<article><h3>${esc(v.term)} <span>${esc(v.en)}</span></h3><p>${esc(v.definition)}</p>${v.example?`<small>${esc(v.example)}</small>`:''}</article>`).join(''):`<p>${t('没有找到，试试英文缩写。','No match. Try an abbreviation.')}</p>`;
  }
  function openGlossary() { $('#glossary-search').value='';showGlossary();$('#glossary-dialog').showModal();$('#glossary-search').focus(); }
  function present() { const active=document.body.classList.toggle('presentation');$('#present-toggle').setAttribute('aria-pressed',String(active));shell();window.scrollTo({top:0,behavior:'instant'}); }
  document.addEventListener('click',event=>{
    const b=event.target.closest('button');if(!b)return;
    if(b.dataset.close)$('#'+b.dataset.close).close();
    if(b.dataset.image){$('#image-dialog').classList.toggle('diagram-view',b.dataset.image.endsWith('.svg'));$('#image-full').src=b.dataset.image;$('#image-full').alt=b.dataset.caption;$('#image-caption').textContent=b.dataset.caption+(b.dataset.image.endsWith('.svg')?t(' · 可左右 / 上下滑动查看',' · Scroll horizontally or vertically to explore'):'');$('#image-dialog').showModal();}
    if(b.dataset.variant){const open=$$('#case-body details[open]').map(d=>d.id);state.variant=b.dataset.variant;showCase();open.forEach(id=>{const d=document.getElementById(id);if(d)d.open=true;});}
    if(b.dataset.move!==undefined){const i=Number(b.dataset.move),j=i+Number(b.dataset.direction);if(j>=0&&j<state.order.length){[state.order[i],state.order[j]]=[state.order[j],state.order[i]];showSort();}}
    if(b.dataset.encodingMethod!==undefined){state.encodingMethod=Number(b.dataset.encodingMethod);showEncoding();}
    if(b.dataset.teacherStep!==undefined){state.teacherStep=Number(b.dataset.teacherStep);showTeacher();}
    if(b.dataset.ragStep!==undefined){state.ragStep=Number(b.dataset.ragStep);showRag();}
    if(b.dataset.scoreStep!==undefined){state.scoreStep=Number(b.dataset.scoreStep);showScore();}
    if(b.dataset.quizAnswer!==undefined){state.quizAnswers[current]=Number(b.dataset.quizAnswer);showQuiz();$(`[data-quiz-answer="${state.quizAnswers[current]}"]`).focus({preventScroll:true});}
    if(b.dataset.sectionJump){document.getElementById(b.dataset.sectionJump)?.scrollIntoView({behavior:'instant',block:'start'});}
    if(b.id==='share-link')shareLesson();
    if(b.id==='load-latest')location.reload();
    if(b.id==='language-toggle')switchLanguage();
    if(b.id==='shuffle-toggle'){state.shuffled=!state.shuffled;showSupervision();}
    if(b.id==='toggle-gold'){state.gold=!state.gold;const open=$$('#case-body details[open]').map(d=>d.id);showCase();open.forEach(id=>{const d=document.getElementById(id);if(d)d.open=true;});}
    if(b.id==='sort-reset'){state.order=['A','B','C','D','E'];showSort();}
    if(b.id==='glossary-open')openGlossary();if(b.id==='present-toggle')present();
    if(b.id==='menu-toggle'){const active=$('#sidebar').classList.toggle('open');b.setAttribute('aria-expanded',String(active));}
    if(b.id==='previous'||b.id==='next'){const i=chapters.findIndex(c=>c[0]===current)+(b.id==='previous'?-1:1);if(chapters[i])navigate(chapters[i][0]);}
    if(b.id==='mark-done'){completed.has(current)?completed.delete(current):completed.add(current);save('docreranker-linear-progress-v2',[...completed]);progress();$(`.nav-item[href="#${current}"]`).classList.toggle('done',completed.has(current));}
    if(b.id==='reset-progress'){completed.clear();save('docreranker-linear-progress-v2',[]);progress();$$('.nav-item.done').forEach(n=>n.classList.remove('done'));}
  });
  document.addEventListener('change',event=>{
    const el=event.target;
    if(el.id==='metric-select'){state.metric=el.value;showMetric();}
    if(el.id==='case-select'){state.caseIndex=Number(el.value);state.gold=false;showCase();}
    if(el.id==='reward-a'||el.id==='reward-b'){state[el.id==='reward-a'?'rewardA':'rewardB']=Number(el.value);showReward();}
    if(el.id==='score-page'){state.scorePage=Number(el.value);showScore();}
    if(el.id==='source-select'){state.source=el.value;showSource(el.value);}
  });
  document.addEventListener('input',event=>{if(event.target.id==='candidate-k'){state.k=Number(event.target.value);showK();}if(event.target.id==='lora-rank'){state.loraRank=Number(event.target.value);showLora();}if(event.target.id==='token-probability'){state.probability=Number(event.target.value);showLoss();}if(event.target.id==='glossary-search')showGlossary(event.target.value);});
  document.addEventListener('keydown',event=>{
    if(event.ctrlKey||event.metaKey||event.altKey||event.defaultPrevented)return;
    if(event.key==='Escape'&&$('dialog[open]')){event.preventDefault();$('dialog[open]').close();return;}
    if(/INPUT|TEXTAREA|SELECT|BUTTON|SUMMARY/.test(event.target.tagName)||event.target.isContentEditable||$('dialog[open]'))return;
    if(event.key==='/'){event.preventDefault();openGlossary();}
    if(event.key.toLowerCase()==='p')present();
    if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();const i=chapters.findIndex(c=>c[0]===current)+(event.key==='ArrowRight'?1:-1);if(chapters[i])navigate(chapters[i][0]);}
  });
  $('.skip-link').addEventListener('click',event=>{event.preventDefault();$('#main').focus();window.scrollTo({top:0,behavior:'instant'});});
  $$('dialog').forEach(d=>d.addEventListener('click',event=>{if(event.target===d){const r=d.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)d.close();}}));
  window.addEventListener('hashchange',()=>{render(location.hash.slice(1));$('#main').focus({preventScroll:true});});
  if(!D||!L?.zh||!L?.en||!I||!G?.zh||!G?.en||!H?.zh||!H?.en){$('#lesson-root').innerHTML='<p>请保留完整网页文件夹。 / Keep the complete classroom folder together.</p>';return;}
  render(location.hash.slice(1)||'rag');
})();
