"""Validate copied source assets and package this classroom without training files."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    root = Path(__file__).resolve().parents[1]
    provenance = json.loads((root / 'assets/provenance.json').read_text())
    for entry in provenance['derivedAssets']:
        assert digest(root / entry['path']) == entry['sha256'], entry['path']
    verification = json.loads((root / 'checks/verification.json').read_text())
    assert verification['status'] == 'passed'
    files = sorted(p for p in root.rglob('*') if p.is_file()
                   and '__pycache__' not in p.parts and not {'public', 'dist'}.intersection(p.relative_to(root).parts)
                   and '.git' not in p.relative_to(root).parts and '.openai' not in p.relative_to(root).parts
                   and not p.name.startswith('preview-') and p.name != 'package-manifest.json')
    assert not any(p.name.startswith('.env') or p.suffix == '.zip' for p in files)
    manifest = {'createdAt': datetime.now(timezone.utc).isoformat(),
                'entrypoint': 'index.html', 'externalDependencies': False,
                'verification': 'checks/verification.json',
                'files': {str(p.relative_to(root)): digest(p) for p in files}}
    manifest_path = root / 'package-manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    output = root.parent / 'teaching-web.zip'
    with ZipFile(output, 'w', compression=ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in [*files, manifest_path]:
            bundle.write(path, root.name + '/' + str(path.relative_to(root)))
    with ZipFile(output) as bundle:
        assert bundle.testzip() is None
        assert 'teaching-web/index.html' in bundle.namelist()
    print(json.dumps({'status': 'passed', 'file': str(output),
                      'files': len(files) + 1, 'bytes': output.stat().st_size,
                      'sha256': digest(output)}, indent=2))


if __name__ == '__main__':
    main()
