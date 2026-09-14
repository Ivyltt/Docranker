"""Refresh classroom results from saved predictions, with no inference/training.

Validate the same 1,658 inputs, strict output parsing, full gold denominators
and reported metrics before changing the website. Keep the visual SFT example.
"""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import math
import sys

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
RUN = PROJECT / 'outputs/reference-informed-v2'
sys.path.insert(0, str(PROJECT / 'src'))
from docreranker.evaluation import evaluate, paired_bootstrap, ranking_metrics

KEYS = {'recall1': 'macro_recall@1', 'recall3': 'macro_recall@3',
        'recall5': 'macro_recall@5', 'mrr': 'mrr', 'ndcg5': 'ndcg@5'}
SOURCES = {
    'base': PROJECT / 'outputs/flash-lite-reference4/evaluation/base-predictions.jsonl',
    'sft': RUN / 'historical1658-arms-v3/sft900_merged/generation/predictions.jsonl',
    'grpo': RUN / 'historical1658-selected6-v2/generation/predictions.jsonl',
}


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.open() if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def close(actual, expected):
    assert math.isclose(actual, expected, abs_tol=1e-12), (actual, expected)


def main():
    data_path = ROOT / 'assets/teaching-data.js'
    data = json.JSONDecoder().raw_decode(data_path.read_text().split('=', 1)[1].lstrip())[0]
    records = rows(PROJECT / 'data/evaluation_candidates.jsonl')
    protocol = read(RUN / 'full-test-evaluation-protocol.json')
    assert len(records) == protocol['queries'] == 1658
    assert sha(PROJECT / 'data/evaluation_candidates.jsonl') == protocol['input_sha256']
    assert all(not r['gold_injected'] and not r['training_candidates'] for r in records)
    predictions = {key: rows(path) for key, path in SOURCES.items()}
    reports = {key: evaluate(records, values) for key, values in predictions.items()}
    summary = read(RUN / 'classroom-test/summary.json')
    names = {'base': 'base', 'sft': 'sft900_merged', 'grpo': 'selected_grpo'}
    for key, report in reports.items():
        assert report['evaluated_queries'] == 1658 and report['skipped_no_gold'] == 0
        assert [r['query_id'] for r in predictions[key]] == [r['query_id'] for r in records]
        saved = summary['variants'][names[key]]
        for name in ['macro_recall@1', 'mrr', 'ndcg@5']:
            close(report['reranker'][name], saved[name])
        assert report['fallback_queries'] == saved['fallback_queries']
    for key, directory in [('sft', 'historical1658-arms-v3/sft900_merged'),
                           ('grpo', 'historical1658-selected6-v2')]:
        saved = read(RUN / directory / 'generation/metrics.json')
        for name, value in reports[key]['reranker'].items():
            close(value, saved['reranker'][name])
    selection = read(RUN / 'evaluation/selection.json')
    assert selection['status'] == 'locked' and selection['selected_step'] == 512
    assert selection['confirmation_quality_used'] is False
    assert summary['selected_grpo_step'] == 512
    labels = {'retrieval': 'ColQwen2 检索原序', 'base': 'Base · 未做本任务 SFT',
              'sft': 'SFT900 merged', 'grpo': 'SFT + GRPO512'}
    data['metrics']['rows'] = []
    for key in ['retrieval', 'base', 'sft', 'grpo']:
        values = reports['base']['baseline'] if key == 'retrieval' else reports[key]['reranker']
        report = reports['base'] if key == 'retrieval' else reports[key]
        data['metrics']['rows'].append({'id': key, 'label': labels[key],
            **{name: values[original] for name, original in KEYS.items()},
            'fallback': None if key == 'retrieval' else report['fallback_queries'],
            'fallbackRate': None if key == 'retrieval' else report['fallback_rate'], 'total': 1658})
    # Use paired query resampling with exactly the reported seed and draw count.
    data['metrics']['paired'] = {}
    for name, before, after in [('sftMinusBase', 'base', 'sft'), ('grpoMinusSft', 'sft', 'grpo')]:
        paired = [{**row, 'baseline': reports[before]['per_query'][i]['reranker']}
                  for i, row in enumerate(reports[after]['per_query'])]
        intervals = paired_bootstrap(paired, (1, 3, 5), samples=1000, seed=42)
        data['metrics']['paired'][name] = {
            key: {'delta': reports[after]['reranker'][metric] - reports[before]['reranker'][metric],
                  **intervals[metric]} for key, metric in KEYS.items()}
    for metric in ['macro_recall@1', 'mrr', 'ndcg@5']:
        key = next(k for k, v in KEYS.items() if v == metric)
        result = data['metrics']['paired']['grpoMinusSft'][key]
        close(result['delta'], summary['grpo_minus_sft']['delta'][metric])
        for bound in ['low', 'high']:
            close(result[bound], summary['grpo_minus_sft']['paired_95_ci'][metric][bound])
    data['metrics']['download'] = 'assets/sources/metrics-current.json'
    data['metrics']['notes'] = ['固定 1,658 题，同一候选图片、提示、解码设置和完整 gold 分母。',
        'SFT 指第 900 步 BF16 合并模型，GRPO 指在该模型基础上训练并选定的第 512 步。',
        '严格格式失败时保留检索原序计分；所有问题保留在分母。',
        '排序指标不直接评估证据说明的事实准确性。']
    data['grpoExperiment'] = {
        'selectedStep': 512, 'completedSteps': 768, 'trainingQueries': 768,
        'generationsPerQuery': 4, 'iterations': 2,
        'selectedExposure': summary['selected_checkpoint_training'],
        'fullExposure': summary['complete_training'],
        'config': read(RUN / 'training/formal/effective-config.json'),
        'top1Hit': summary['top1_hit'],
        'selectionRule': '验证阶段按预定规则选定第 512 步；测试成绩不用于选点。',
    }
    indexed = {r['query_id']: r for r in records}
    pred_by_id = {k: {r['query_id']: r for r in values} for k, values in predictions.items()}
    # Preserve existing teaching cases, and add the two fixed GRPO contrast cases.
    new_cases = [
        ('eval:2020.acl-main.45.pdf:0', 'GRPO 改善：把相关页提到首位',
         '论文提出如何计算加权交叉熵损失中的系数 α？',
         '同样五张图，SFT 把标注相关的图 1 排第 2，GRPO 提到第 1；原标签 Recall@1 从 0 到 1。比较的是证据页位置，不是模型文字是否准确解释了 α。'),
        ('eval:2019713412.pdf:1', 'GRPO 退步：平均提高不等于每题提高',
         '在马来西亚，被告能否对认罪协商形成的定罪与判刑提出上诉？若能，有哪些理由？',
         '原标签相关页是图 2。SFT 排在首位，GRPO 改把图 1 放在首位；Recall@1 从 1 到 0。两份输出格式都有效，仍会出现排序退步。')]
    existing = {c['id'] for c in data['cases']}
    from PIL import Image
    for qid, title, zh, lesson in new_cases:
        if qid in existing:
            continue
        record = indexed[qid]
        pages = []
        for i, candidate in enumerate(record['candidates'], 1):
            source = Path(candidate['image'])
            if not source.is_absolute():
                source = PROJECT / source
            stem = hashlib.sha256(source.read_bytes()).hexdigest()[:20]
            original = ROOT / 'assets/pages' / (stem + '.png')
            original.write_bytes(source.read_bytes())
            image = original.with_suffix('.jpg')
            with Image.open(original) as picture:
                width, height = picture.size
                picture.convert('RGB').save(image, quality=85, optimize=True)
            identity = json.loads(candidate['page_id'])
            pages.append({'index': i, 'pageId': candidate['page_id'], 'document': identity[1],
                'pageNumber': identity[2], 'pageNumberLabel': '数据记录页码', 'printedPage': None,
                'image': str(image.relative_to(ROOT)), 'imageOriginal': str(original.relative_to(ROOT)),
                'width': width, 'height': height, 'source': str(source.relative_to(PROJECT)),
                'sourceSha256': sha(source), 'score': candidate['score'],
                'isGold': candidate['page_id'] in record['positive_page_ids'], 'reviewedEvidence': None})
        data['cases'].append({'id': qid, 'title': title, 'query': record['query'], 'queryZh': zh,
            'goldPageIds': record['positive_page_ids'],
            'goldCandidateIndices': [p['index'] for p in pages if p['isGold']],
            'pages': pages, 'variants': {}, 'lesson': lesson,
            'review': {'summary': '依据保存的原标签、排列与原文比较；不把模型说明视为人工确认的答案。',
                       'notes': [], 'scope': '这两份新模型说明未新增逐句事实审核。'},
            'selectionReason': '从同一 1,658 题中，按已保存的固定规则取该改善／退步条件下 query_id 最小的一题。'})
    for case in data['cases']:
        record = indexed[case['id']]
        candidate_ids = [r['page_id'] for r in record['candidates']]
        mapping = {page: i for i, page in enumerate(candidate_ids, 1)}
        for key in ['retrieval', 'base', 'sft', 'grpo']:
            prediction = None if key == 'retrieval' else pred_by_id[key][case['id']]
            ranking = candidate_ids if prediction is None else prediction['ranked_page_ids']
            case['variants'][key] = {'ranking': [mapping[p] for p in ranking],
                'rawOutput': '' if prediction is None else prediction['raw_response'],
                'fallback': False if prediction is None else prediction['fallback'],
                'fallbackReason': None if prediction is None else prediction['fallback_reason'],
                'metrics': ranking_metrics(ranking, set(record['positive_page_ids'])),
                'source': 'data/evaluation_candidates.jsonl' if prediction is None else str(SOURCES[key].relative_to(PROJECT))}
        if case['review'].get('reviewedVariants'):
            # The same chart-value claims persist in the merged SFT outputs.
            # Keep their factual checks; apply no old review to the new GRPO.
            case['review']['scope'] = '原图中的数字、类别与人群核查仍适用于当前 SFT 说明中保留的 47%／58% 误读；未对所有新模型说明逐句审核，也未估计全部测试题的说明准确率。'
    data['snapshot']['run'] = 'reference-informed-v2 / fixed-test1658'
    data['snapshot']['variants'] = ['retrieval', 'base', 'sft', 'grpo']
    data['snapshot']['createdAt'] = datetime.now(timezone.utc).isoformat()
    data['snapshot']['description'] = '固定测试集横向比较：Base、SFT900 merged、SFT + GRPO512。'
    data_path.write_text('// Verified saved predictions; no new model calls.\nwindow.CLASS_DATA = '
                        + json.dumps(data, ensure_ascii=False, indent=2) + ';\n')
    sources = ROOT / 'assets/sources'
    (sources / 'metrics-current.json').write_text(json.dumps(data['metrics'], ensure_ascii=False, indent=2)+'\n')
    (sources / 'current-test-report.md.txt').write_bytes((RUN / 'classroom-test/report.md').read_bytes())
    # Keep only the classroom comparison in the downloadable teaching report.
    report_text = (sources / 'current-test-report.md.txt').read_text()
    report_text = report_text.replace('此测试集此前已被观察，本结果属于重复测试；', '')
    report_text = report_text.replace('详见 [标注输入说明](docs/annotation-inputs.md)。', '')
    (sources / 'current-test-report.md.txt').write_text(report_text)
    (sources / 'grpo-config-snapshot.json').write_text(json.dumps(data['grpoExperiment']['config'],indent=2)+'\n')
    provenance = read(ROOT / 'assets/provenance.json')
    provenance['includesCurrentGrpoResults'] = True
    provenance['teachingDatasetIncludesGrpoResults'] = True
    provenance['currentResults'] = {'queries': 1658, 'models': labels,
        'sources': {str(p.relative_to(PROJECT)): sha(p) for p in SOURCES.values()},
        'candidatesSha256': protocol['input_sha256'], 'selectedGrpoStep': 512,
        'validation': 'Strict parsing, full-gold ranking metrics and paired intervals independently recomputed.'}
    # Packaging checks every generated image and evidence file against its bytes.
    provenance['derivedAssets'] = [
        {'path': str(p.relative_to(ROOT)), 'sha256': sha(p), 'bytes': p.stat().st_size}
        for p in sorted((ROOT / 'assets').rglob('*'))
        if p.is_file() and p.suffix in {'.png','.jpg','.svg'}]
    (ROOT / 'assets/provenance.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'status': 'verified', 'queries': 1658, 'cases': len(data['cases']),
        'recall1': {r['id']: r['recall1'] for r in data['metrics']['rows']},
        'pairedGrpoMinusSft': data['metrics']['paired']['grpoMinusSft']['recall1']}, indent=2))


if __name__ == '__main__':
    main()
