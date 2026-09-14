"""Freeze a clean project export before committing the classroom release.

Read source bytes only: this never runs training or changes the shared project.
The tracked ZIP makes deployment reproducible while GRPO work continues.
"""
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent


def main():
    files = {}
    for directory in ['src', 'configs']:
        for path in (PROJECT / directory).rglob('*'):
            if path.is_file() and path.suffix in {'.py', '.json'} and '__pycache__' not in path.parts:
                files[str(path.relative_to(PROJECT))] = path.read_bytes()
    # Keep core classroom helpers; omit historical experiment/report variants.
    for name in ['prepare_training_data', 'retrieve_candidates', 'teaching_example']:
        path = PROJECT / 'scripts' / (name + '.py')
        files['scripts/' + path.name] = path.read_bytes()
    for name in ['annotation', 'data', 'retrieval', 'prompts', 'evaluation',
                 'inference', 'rewards', 'prepare_training_data']:
        path = PROJECT / 'tests' / ('test_' + name + '.py')
        if path.exists():
            files['tests/' + path.name] = path.read_bytes()
    for name in ['pyproject.toml', 'uv.lock']:
        files[name] = (PROJECT / name).read_bytes()
    files['README.md'] = (ROOT / 'PROJECT_README.md').read_bytes()
    files['.gitignore'] = b'.env\n.env.*\n.venv/\n__pycache__/\n*.pyc\n.pytest_cache/\n.ruff_cache/\ndata/\nmodels/\noutputs/\n*.egg-info/\n'
    files['SOURCE_MANIFEST.json'] = (json.dumps({
        'description': 'Project source snapshot; GRPO implementation is under active revision.',
        'files': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
    }, indent=2) + '\n').encode()
    target = ROOT / '.openai/project-code.zip'
    target.parent.mkdir(exist_ok=True)
    with ZipFile(target, 'w', ZIP_DEFLATED) as bundle:
        for name, data in sorted(files.items()):
            # Stable ZIP metadata avoids a different release hash on each build.
            info = ZipInfo('docreranker/' + name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            bundle.writestr(info, data)
    print(json.dumps({'files': len(files), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
