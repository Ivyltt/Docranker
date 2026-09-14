"""Check the actual static build, including its links and update notification."""
from pathlib import Path
import argparse
import json
from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', required=True)
    parser.add_argument('--url', default='http://127.0.0.1:8770/')
    args = parser.parse_args()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.browser,
                                   args=['--no-sandbox', '--disable-dev-shm-usage'])
        context = browser.new_context(viewport={'width': 1440, 'height': 1050})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        # Install before creating the interval, so the test clock controls it.
        page.clock.install()
        for lang in ['zh', 'en']:
            for chapter in ['rag','data','retrieve','labels','sft','results','grpo','lab']:
                page.goto(args.url + '?lang=' + lang + '#' + chapter)
                expect(page.locator(f'.nav-item[href="#{chapter}"]')).to_have_attribute('aria-current','page')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                urls = page.eval_on_selector_all('[href],[src]',
                    'nodes => nodes.map(n => n.getAttribute("href") || n.getAttribute("src"))')
                for url in urls:
                    if url and not url.startswith(('#','http:','https:','data:')):
                        response = context.request.get(args.url + url)
                        assert response.status == 200, (chapter, url, response.status)
            expect(page.locator('[href="project-code.zip"]')).to_be_visible()
            page.set_viewport_size({'width':320, 'height':800})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            expect(page.locator('#github-link')).to_have_attribute(
                'href', 'https://github.com/Ivyltt/Docranker')
            expect(page.locator('#github-link')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.set_viewport_size({'width':1440, 'height':1050})
        # Verify the copied link opens the public lesson in the selected language.
        page.evaluate("Object.defineProperty(navigator, 'share', {value:undefined, configurable:true})")
        page.evaluate("navigator.clipboard.writeText = async text => {window.sharedLesson = text}")
        page.locator('#share-link').click()
        assert page.evaluate('window.sharedLesson') == (
            'https://docreranker-classroom.dreamy-rose-3664.chatgpt.site/?lang=en#lab')
        page.route('**/version.json', lambda route: route.fulfill(json={'version':'next-test-version'}))
        page.clock.fast_forward(61000)
        expect(page.locator('#update-notice')).to_be_visible()
        assert not errors, errors
        browser.close()
    print(json.dumps({'status':'passed','publicLinks':True,'versionNotification':True,
                      'languages':['zh','en'],'javascriptErrors':[]}))


if __name__ == '__main__':
    main()
