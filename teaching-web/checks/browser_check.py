"""Check the complete bilingual classroom in a real offline browser."""
import argparse
import json
import re
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

CHAPTERS = ['rag', 'data', 'retrieve', 'labels', 'sft', 'results', 'grpo', 'lab']
FOUNDATION_PARTS = {
    'rag': ['ragIntro', 'ragAfter'],
    'data': ['dataIntro', 'dataAfter'],
    'retrieve': ['retrievalIntro', 'scoreIntro', 'kIntro', 'kAfter'],
    'labels': ['labelsAfter'],
    'results': ['resultsIntro', 'resultsAfter'],
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser')
    parser.add_argument('--output', type=Path, default=Path('/tmp/docreranker-web-v5-check'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    errors, failed, network, lengths = [], [], [], {}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.browser, args=['--no-sandbox', '--disable-dev-shm-usage'])
        context = browser.new_context(viewport={'width': 1440, 'height': 1050})
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('requestfailed', lambda req: failed.append(req.url))
        page.on('request', lambda req: network.append(req.url) if req.url.startswith(('http:', 'https:')) else None)
        page.goto(root.joinpath('index.html').as_uri())
        expect(page.locator('.nav-item')).to_have_count(8)
        expect(page.locator('.nav-item[href="#rag"]')).to_have_attribute('aria-current', 'page')

        def go(chapter):
            page.evaluate('(id) => location.hash = id', chapter)
            expect(page.locator(f'.nav-item[href="#{chapter}"]')).to_have_attribute('aria-current', 'page')

        def open_detail(identifier):
            page.locator(f'#{identifier}').evaluate('(d) => d.open = true')

        def set_range(identifier, value):
            control = page.locator(f'#{identifier}')
            expect(control).to_be_visible()
            control.evaluate('''(input, value) => {
                input.value = String(value);
                input.dispatchEvent(new Event('input', {bubbles: true}));
            }''', value)
            expect(control).to_have_value(str(value))

        def screenshot(name):
            # Full-page capture must load images below the current viewport, too.
            loaded = page.evaluate('''async () => Promise.all(
                [...document.querySelectorAll('img[src]')].map(async img => {
                    img.loading = 'eager';
                    try { await img.decode(); return img.naturalWidth > 0; }
                    catch (_) { return false; }
                })
            )''')
            assert all(loaded), (name, 'an image failed to decode')
            page.evaluate('scrollTo(0, 0)')
            page.screenshot(path=str(args.output / name), full_page=True, animations='disabled')

        def core_visible(chapter, lang):
            # Foundational explanations must be available without opening details.
            design_sections = {
                'retrieve': ['encoding-comparison'],
                'labels': ['teacher-pipeline', 'refinement-comparison'],
            }
            for identifier in design_sections.get(chapter, []):
                expect(page.locator(f'#{identifier}')).to_be_visible()
                assert not page.locator(f'#{identifier}').evaluate(
                    '(node) => !!node.closest("details:not([open])")'
                ), (chapter, identifier, 'core design explanation is collapsed')
            parts = FOUNDATION_PARTS.get(chapter, [])
            if chapter in ('sft', 'grpo'):
                headings = page.evaluate('''([lang, chapter]) => {
                    const doc = new DOMParser().parseFromString(CLASS_LESSONS[lang][chapter], 'text/html');
                    return [...doc.querySelectorAll('h3')].map(h => h.textContent);
                }''', [lang, chapter])
            elif parts:
                headings = page.evaluate('''([lang, parts]) => parts.flatMap(part => {
                    const doc = new DOMParser().parseFromString(CLASS_FOUNDATIONS[lang][part], 'text/html');
                    return [...doc.querySelectorAll('h2')].map(h => h.textContent);
                })''', [lang, parts])
            else:
                return
            assert headings, (chapter, 'missing core headings')
            for heading in headings:
                expect(page.locator('#lesson-root').get_by_role('heading', name=heading, exact=True)).to_be_visible()

        def quiz_check(chapter, lang):
            if chapter == 'lab':
                return
            answers = page.locator('[data-quiz-answer]')
            expect(answers).to_have_count(3)
            expect(page.locator('[data-quiz-answer][data-correct="true"]')).to_have_count(1)
            feedback = page.locator('#quiz-feedback')
            for index in range(3):
                answer = page.locator(f'[data-quiz-answer="{index}"]')
                correct = answer.get_attribute('data-correct')
                assert correct in ('true', 'false')
                answer.click()
                expect(feedback).not_to_be_empty()
                expect(feedback).to_be_visible()
                expect(feedback).to_have_attribute('data-correct', correct)
                if lang == 'en':
                    english_check()

        def english_check():
            text = page.locator('#lesson-root').evaluate('''root => {
                const clone = root.cloneNode(true);
                clone.querySelectorAll('pre').forEach(node => node.remove());
                return clone.textContent;
            }''')
            assert not re.search(r'[\u3400-\u9fff]', text), re.findall(r'.{0,20}[\u3400-\u9fff].{0,30}', text)[:6]
            text = page.locator('body').evaluate('''root => {
                const clone = root.cloneNode(true);
                clone.querySelectorAll('#lesson-root,#language-toggle,noscript,script').forEach(n=>n.remove());
                return clone.textContent;
            }''')
            assert not re.search(r'[\u3400-\u9fff]', text), re.findall(r'.{0,10}[\u3400-\u9fff].{0,20}', text)[:6]

        def diagram_check(stem, lang):
            # Diagrams must work as local SVG images, use the chosen language,
            # and open the original diagram in the existing image viewer.
            source = f'assets/{stem}-{lang}.svg'
            diagram = page.locator(f'#lesson-root img[src="{source}"]')
            expect(diagram).to_have_count(1)
            expect(diagram).to_be_visible()
            assert diagram.evaluate('''async img => {
                img.loading = 'eager';
                try { await img.decode(); }
                catch (_) { return false; }
                return img.complete && img.naturalWidth > 0 && img.naturalHeight > 0;
            }'''), source
            svg = (root / source).read_text()
            assert '<svg' in svg
            if lang == 'en':
                visible_svg_text = page.evaluate('''svg => {
                    const doc = new DOMParser().parseFromString(svg, 'image/svg+xml');
                    return [...doc.querySelectorAll('text,title,desc')].map(n => n.textContent).join(' ');
                }''', svg)
                assert not re.search(r'[\u3400-\u9fff]', visible_svg_text), source
            page.locator(f'#lesson-root button[data-image="{source}"]').click()
            expect(page.locator('#image-dialog')).to_be_visible()
            expect(page.locator('#image-full')).to_have_attribute('src', source)
            page.wait_for_function('''() => {
                const img = document.querySelector('#image-full');
                return img.complete && img.naturalWidth > 0;
            }''')
            if lang == 'en':
                english_check()
            page.keyboard.press('Escape')
            expect(page.locator('#image-dialog')).not_to_be_visible()

        def diagram_language_check(stem, lang):
            # Language switches preserve scroll. A newly inserted diagram above
            # that position may still be waiting for lazy loading; decode it
            # before checking its natural size and displayed dimensions.
            diagram = page.locator(f'#lesson-root img[src="assets/{stem}-{lang}.svg"]')
            expect(diagram).to_have_count(1)
            assert diagram.evaluate('''async img => {
                img.loading = 'eager';
                try { await img.decode(); }
                catch (_) { return false; }
                return img.complete && img.naturalWidth > 0 && img.naturalHeight > 0;
            }''')
            expect(diagram).to_be_visible()

        def design_steps(attribute, detail_id, count, initial, lang):
            choices = page.locator(f'[{attribute}]')
            expect(choices).to_have_count(count)
            expect(page.locator(f'[{attribute}="{initial}"]')).to_have_attribute('aria-pressed', 'true')
            content = []
            for index in range(count):
                button = page.locator(f'[{attribute}="{index}"]')
                button.click()
                expect(button).to_have_attribute('aria-pressed', 'true')
                expect(page.locator(f'[{attribute}][aria-pressed="true"]')).to_have_count(1)
                panel = page.locator(f'#{detail_id}')
                expect(panel).to_be_visible()
                expect(panel).not_to_be_empty()
                content.append(panel.inner_text())
                if lang == 'en':
                    english_check()
            assert len(set(content)) == count, (attribute, 'distinct teaching explanations are required')
            page.locator(f'[{attribute}="{initial}"]').click()

        for lang in ['zh', 'en']:
            actual = page.locator('html').get_attribute('lang')
            if (lang == 'en') != (actual == 'en'):
                page.locator('#language-toggle').click()
            lengths[lang] = {}
            for chapter in CHAPTERS:
                go(chapter)
                lengths[lang][chapter] = len(page.locator('#lesson-root').inner_text())
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), (lang, chapter)
                assert 'undefined' not in page.locator('#lesson-root').inner_text(), (lang, chapter)
                assert 'NaN' not in page.locator('#lesson-root').inner_text(), (lang, chapter)
                if lang == 'en':
                    english_check()
                if chapter == 'labels':
                    expect(page.locator('#supervision-boundary')).to_be_visible()
                    expect(page.locator('#supervision-boundary')).to_contain_text('真实候选图片' if lang == 'zh' else 'real candidate images')
                if chapter == 'sft':
                    expect(page.locator('#sft-visual-input')).to_be_visible()
                    expect(page.locator('#sft-visual-input')).to_contain_text('多模态训练' if lang == 'zh' else 'multimodal')
                core_visible(chapter, lang)
                quiz_check(chapter, lang)
                # Include links nested in closed details.
                urls = page.eval_on_selector_all('[href],[src]', '(nodes)=>nodes.map(n=>n.getAttribute("href")||n.getAttribute("src"))')
                for url in urls:
                    if url and not url.startswith(('#','http:', 'https:', 'data:')):
                        assert (root / url.split('#')[0]).exists(), (chapter, url)
            go('rag')
            expect(page.locator('[data-rag-step]')).to_have_count(5)
            rag_details = []
            for step in range(5):
                page.locator(f'[data-rag-step="{step}"]').click()
                expect(page.locator('#rag-detail')).not_to_be_empty()
                rag_details.append(page.locator('#rag-detail').inner_text())
                if lang == 'en':
                    english_check()
            assert len(set(rag_details)) == 5, 'Each RAG stage needs a distinct explanation'
            page.locator('[data-rag-step="0"]').click()
            screenshot(f'{lang}-rag.png')
            go('data')
            screenshot(f'{lang}-data.png')
            open_detail('raw-data')
            actual = json.loads(page.locator('#raw-data pre').inner_text())
            original = json.loads((root / 'assets/sources/reits-original.json').read_text())
            assert actual == original
            assert actual['positive_passages'][0]['page_id'] == 4
            page.locator('#data-example [data-image]').click()
            expect(page.locator('#image-dialog')).to_be_visible()
            expect(page.locator('#image-full')).to_have_attribute('src', re.compile(r'\.png$'))
            expect(page.locator('#image-full')).to_be_visible()
            page.wait_for_function('''() => {
                const img = document.querySelector('#image-full');
                return img.complete && img.naturalWidth > 0;
            }''')
            page.keyboard.press('Escape')
            go('retrieve')
            design_steps('data-encoding-method', 'encoding-detail', 4, 3, lang)
            diagram_check('encoding-paths', lang)
            expect(page.locator('#score-page')).to_be_visible()
            for candidate, expected in [('0', '2.400'), ('1', '1.500')]:
                page.locator('#score-page').select_option(candidate)
                for step in range(3):
                    page.locator(f'[data-score-step="{step}"]').click()
                expect(page.locator('#score-result')).to_contain_text(expected)
            page.locator('#score-page').select_option('0')
            set_range('candidate-k', 5)
            expect(page.locator('#k-result')).to_contain_text(re.compile(r'2\s*/\s*3'))
            set_range('candidate-k', 7)
            expect(page.locator('#k-result')).to_contain_text(re.compile(r'3\s*/\s*3'))
            set_range('candidate-k', 1)
            expect(page.locator('#k-result')).to_contain_text(re.compile(r'0\s*/\s*3'))
            set_range('candidate-k', 5)
            expect(page.locator('#candidate-gallery .page-card')).to_have_count(5)
            assert page.locator('#candidate-gallery .page-card').first.locator('p').inner_text().startswith('数据页号 4' if lang=='zh' else 'Dataset page ID 4')
            # Teaching controls must never relabel or alter the saved real candidates.
            expect(page.locator('#candidate-gallery .gold-tag')).to_have_count(1)
            screenshot(f'{lang}-retrieve.png')
            go('labels')
            design_steps('data-teacher-step', 'teacher-detail', 3, 0, lang)
            diagram_check('teacher-notes-flow', lang)
            screenshot(f'{lang}-labels.png')
            # State persists across languages, so restore saved training order if needed.
            if page.locator('#shuffle-toggle').get_attribute('aria-pressed') != 'true':
                page.locator('#shuffle-toggle').click()
            expect(page.locator('#supervision-preview')).to_contain_text('[3,2,1,5,4]')
            page.locator('#shuffle-toggle').click()
            expect(page.locator('#supervision-preview')).to_contain_text('[1,2,3,4,5]')
            page.locator('#shuffle-toggle').click()
            open_detail('supervision-details')
            saved_target = page.evaluate('CLASS_DATA.sftExample.messages.at(-1).content')
            assert page.locator('#supervision-details pre').inner_text() == saved_target
            go('sft')
            assert page.locator('#loss-lab').get_attribute('open') is not None
            set_range('token-probability', 80)
            expect(page.locator('#loss-value')).to_contain_text('0.223')
            set_range('lora-rank', 8)
            expect(page.locator('#lora-count')).to_contain_text('16,000')
            set_range('lora-rank', 16)
            expect(page.locator('#lora-count')).to_contain_text('32,000')
            set_range('lora-rank', 8)
            screenshot(f'{lang}-sft.png')
            go('results')
            for metric in ['recall1','recall3','recall5','mrr','ndcg5']:
                page.locator('#metric-select').select_option(metric)
                expect(page.locator('.bar-row')).to_have_count(3)
                assert 'NaN' not in page.locator('#metric-chart').inner_text()
            open_detail('replay-details')
            for case in range(3):
                page.locator('#case-select').select_option(str(case))
                expect(page.locator('#case-body .gold-tag')).to_have_count(0)
                for variant in ['retrieval','base','sft']:
                    page.locator(f'[data-variant="{variant}"]').click()
                    expect(page.locator('#case-body .rank-chip')).to_have_count(5)
                    if variant != 'retrieval':
                        open_detail('case-output')
                        saved_output = page.evaluate('([i,v])=>CLASS_DATA.cases[i].variants[v].rawOutput',[case,variant])
                        assert page.locator('#case-output pre').inner_text() == saved_output
                page.locator('#toggle-gold').click()
                expect(page.locator('.mini-metrics')).to_be_visible()
                assert 'NaN' not in page.locator('.mini-metrics').inner_text()
                open_detail('case-review')
                if case == 1:
                    expect(page.locator('#case-review')).to_contain_text('47%')
                if case == 2:
                    expect(page.locator('#case-review')).to_contain_text('58%')
                if lang == 'en':
                    english_check()
            open_detail('metrics-lab')
            page.locator('#sort-reset').click()
            page.locator('[data-move="1"][data-direction="-1"]').click()
            expect(page.locator('#sort-metrics')).to_contain_text('1/2 = 0.5')
            assert page.locator('.sort-item b').first.inner_text() == 'B'
            go('grpo')
            assert page.locator('#reward-lab').get_attribute('open') is not None
            page.locator('#reward-a').select_option('0')
            page.locator('#reward-b').select_option('0')
            expect(page.locator('#reward-result')).to_contain_text('相对优势均为 0' if lang=='zh' else 'advantages are zero')
            page.locator('#reward-a').select_option('3')
            expect(page.locator('#reward-result')).to_contain_text('0.00000')
            screenshot(f'{lang}-grpo.png')
            go('lab')
            for option in page.locator('#source-select option').all():
                page.locator('#source-select').select_option(option.get_attribute('value'))
                open_detail('selected-source')
                expect(page.locator('#source-view pre')).not_to_be_empty()
                if lang=='en':
                    english_check()
            # All practice commands should remain usable and copy exact text.
            page.locator('#lesson-root details').first.evaluate('(d)=>d.open=true')
            page.locator('.copy-code').first.click()
            expect(page.locator('#toast')).to_contain_text('已复制' if lang=='zh' else 'Copied')
            page.locator('#glossary-open').click()
            page.locator('#glossary-search').fill('LoRA')
            expect(page.locator('#glossary-results')).to_contain_text('冻结' if lang=='zh' else 'rank')
            page.keyboard.press('Escape')
            page.set_viewport_size({'width':390,'height':844})
            for chapter in CHAPTERS:
                go(chapter)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'), (lang,chapter,'390px')
            go('rag')
            screenshot(f'{lang}-mobile.png')
            go('retrieve')
            screenshot(f'{lang}-retrieve-mobile.png')
            go('labels')
            screenshot(f'{lang}-labels-mobile.png')
            go('retrieve')
            page.locator('#menu-toggle').click()
            expect(page.locator('#sidebar')).to_have_class('sidebar open')
            page.locator('.nav-item[href="#labels"]').click()
            expect(page.locator('#sidebar')).to_have_class('sidebar')
            page.set_viewport_size({'width':320,'height':800})
            for chapter in CHAPTERS:
                go(chapter)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'), (lang,chapter,'320px')
            page.set_viewport_size({'width':1440,'height':1050})

        # Switching language keeps the lesson, selected case, expanded sections and interaction state.
        go('results')
        page.locator('#metric-select').select_option('recall3')
        open_detail('replay-details')
        page.locator('#case-select').select_option('1')
        page.locator('#toggle-gold').click()
        open_detail('case-review')
        page.locator('#language-toggle').click()
        expect(page.locator('.nav-item[href="#results"]')).to_have_attribute('aria-current','page')
        expect(page.locator('#metric-select')).to_have_value('recall3')
        expect(page.locator('#case-select')).to_have_value('1')
        assert page.locator('#replay-details').get_attribute('open') is not None
        assert page.locator('#case-review').get_attribute('open') is not None
        expect(page.locator('#toggle-gold')).to_have_attribute('aria-pressed','true')
        assert 'lang=zh' in page.url

        # New arithmetic and pipeline controls survive a language rerender.
        go('retrieve')
        page.locator('[data-encoding-method="1"]').click()
        page.locator('#score-page').select_option('1')
        page.locator('[data-score-step="2"]').click()
        set_range('candidate-k', 7)
        page.locator('[data-quiz-answer="1"]').click()
        page.locator('#language-toggle').click()
        expect(page.locator('[data-encoding-method="1"]')).to_have_attribute('aria-pressed', 'true')
        expect(page.locator('#encoding-detail')).not_to_be_empty()
        diagram_language_check('encoding-paths', 'en')
        expect(page.locator('#score-page')).to_have_value('1')
        expect(page.locator('#score-result')).to_contain_text('1.500')
        expect(page.locator('#candidate-k')).to_have_value('7')
        expect(page.locator('#k-result')).to_contain_text(re.compile(r'3\s*/\s*3'))
        expect(page.locator('#quiz-feedback')).not_to_be_empty()
        expect(page.locator('#quiz-feedback')).to_have_attribute('data-correct', 'false')
        english_check()
        go('sft')
        set_range('lora-rank', 16)
        set_range('token-probability', 80)
        page.locator('#language-toggle').click()
        expect(page.locator('#lora-rank')).to_have_value('16')
        expect(page.locator('#lora-count')).to_contain_text('32,000')
        expect(page.locator('#token-probability')).to_have_value('80')
        expect(page.locator('#loss-value')).to_contain_text('0.223')
        go('rag')
        page.locator('[data-rag-step="3"]').click()
        page.locator('#language-toggle').click()
        expect(page.locator('[data-rag-step="3"]')).to_have_attribute('aria-pressed','true')
        english_check()
        go('labels')
        page.locator('[data-teacher-step="2"]').click()
        page.locator('#language-toggle').click()
        expect(page.locator('[data-teacher-step="2"]')).to_have_attribute('aria-pressed', 'true')
        expect(page.locator('#teacher-detail')).not_to_be_empty()
        diagram_language_check('teacher-notes-flow', 'zh')
        page.locator('#language-toggle').click()
        expect(page.locator('[data-teacher-step="2"]')).to_have_attribute('aria-pressed', 'true')
        diagram_language_check('teacher-notes-flow', 'en')
        english_check()
        go('rag')
        page.locator('#language-toggle').click()
        page.reload()
        expect(page.locator('html')).to_have_attribute('lang','zh-CN')
        page.locator('#mark-done').click()
        expect(page.locator('#progress-label')).to_contain_text('1 / 8')
        page.locator('#language-toggle').click()
        page.reload()
        expect(page.locator('html')).to_have_attribute('lang','en')
        expect(page.locator('#progress-label')).to_contain_text('1 / 8')
        go('rag')
        page.locator('#present-toggle').click()
        expect(page.locator('body')).to_have_class('presentation')
        assert page.evaluate('scrollY')==0
        screenshot('projection.png')
        page.locator('#present-toggle').click()
        for old,new in [('start','rag'),('task','retrieve'),('baseline','results'),('sources','lab')]:
            page.evaluate('(id)=>location.hash=id',old)
            expect(page.locator(f'.nav-item[href="#{new}"]')).to_have_attribute('aria-current','page')
        assert not errors, errors
        assert not failed, failed
        assert not network, network
        browser.close()
    before_path=Path('/tmp/class-web-length-before.json')
    before=json.loads(before_path.read_text()) if before_path.exists() else None
    report={'status':'passed','version':'teaching-bilingual-v5','chapters':8,'languages':['zh','en'],
            'mode':'file:// offline','viewports':['1440x1050','390x844','320x800','projection'],
            'javascriptErrors':errors,'failedRequests':failed,'httpRequests':network,
            'defaultTextCharacters':lengths,'previousDefaultTextCharacters':before,
            'checks':['RAG opening and five-stage walkthrough','visible core teaching',
                      'four document encoding routes and distinct explanations',
                      'per-page notes, refinement and target assembly steps','visual teacher preparation and visual student SFT explicitly distinguished',
                      'default-visible encoding and teacher rationale sections',
                      'bilingual local SVG diagrams and original diagram zoom',
                      'encoding and teacher selections persist across language switches',
                      'MaxSim row-max-and-sum arithmetic','top-K coverage and unchanged gold',
                      'LoRA rank arithmetic','chapter quiz feedback',
                      'original source record','page4-to-slot3 mapping','exact saved model outputs',
                      'English completeness','language persistence and expanded state','all 3 cases',
                      'new teaching interaction state across languages','original PNG zoom',
                      'default-open loss and reward labs','loss slider','metric selection','manual ranking',
                      'equal rewards','source downloads','code copy','local links','mobile menu',
                      'progress persistence','legacy chapter links']}
    (args.output/'browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
