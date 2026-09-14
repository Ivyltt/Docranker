"""Freeze a clean project export before committing the classroom release.

Read source bytes only: this never runs training or changes the shared project.
The tracked ZIP makes deployment reproducible while GRPO work continues.
"""
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
import argparse
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-dir', type=Path, default=ROOT.parent,
                        help='student project checkout containing SOURCE_MANIFEST.json')
    args = parser.parse_args()
    project = args.project_dir.resolve()
    if not (project / 'SOURCE_MANIFEST.json').exists():
        parser.error('Pass --project-dir pointing to the student GitHub checkout; do not export the shared experiment checkout')
    files = {}
    for directory in ['src', 'configs']:
        for path in (project / directory).rglob('*'):
            if path.is_file() and path.suffix in {'.py', '.json'} and '__pycache__' not in path.parts:
                files[str(path.relative_to(project))] = path.read_bytes()
    # Keep core classroom helpers; omit historical experiment/report variants.
    for name in ['prepare_training_data', 'retrieve_candidates', 'teaching_example', 'prepare_grpo_data']:
        path = project / 'scripts' / (name + '.py')
        files['scripts/' + path.name] = path.read_bytes()
    for name in ['annotation', 'data', 'retrieval', 'prompts', 'evaluation',
                 'inference', 'rewards', 'prepare_training_data', 'prepare_grpo_data']:
        path = project / 'tests' / ('test_' + name + '.py')
        if path.exists():
            files['tests/' + path.name] = path.read_bytes()
    for name in ['pyproject.toml', 'uv.lock', '.gitignore', '.env.example']:
        files[name] = (project / name).read_bytes()
    files['README.md'] = (ROOT / 'PROJECT_README.md').read_bytes()
    # Preserve the reviewed run settings separately from generic course defaults.
    for path in (project / 'experiment').iterdir():
        if path.is_file() and path.suffix in {'.json', '.md'}:
            files['experiment/' + path.name] = path.read_bytes()
    files['SOURCE_MANIFEST.json'] = (json.dumps({
        'description': 'Classroom implementation snapshot; experiment/ contains completed-run settings.',
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
