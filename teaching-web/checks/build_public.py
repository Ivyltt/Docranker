"""Build only classroom runtime assets; preserve the reviewed source folder.

The deployable dist/ directory excludes developer checks, screenshots,
preparation notes and unused snapshots. Each JavaScript/CSS filename includes
its content hash so a new deployment never reuses an old script from cache.
No training, prediction or teacher API request is performed by this build.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "dist"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build() -> None:
    config = json.loads((ROOT / "site-config.json").read_text())
    url = config.get("githubUrl")
    if url and not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?", url):
        raise ValueError("githubUrl must identify the actual project repository")
    # Delete only this generated output directory, never source or project data.
    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    PUBLIC.mkdir()
    main_files = ["index.html", "styles.css", "app.js", "lesson-content.js",
                  "foundation-content.js", "design-content.js",
                  "assets/teaching-data.js", "assets/teaching-i18n.js"]
    for name in main_files:
        target = PUBLIC / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    shutil.copytree(ROOT / "assets/pages", PUBLIC / "assets/pages")
    for source in (ROOT / "assets").glob("*.svg"):
        shutil.copy2(source, PUBLIC / "assets" / source.name)
    sources = PUBLIC / "assets/sources"
    sources.mkdir()
    # These source files are linked from the lesson, rather than a copy of the
    # entire training project (which contains private runtime credentials/data).
    names = [p.name for p in (ROOT / "assets/sources").glob("src__*.txt")]
    names += ["reits-original.json", "training-config-snapshot.json",
              "docs__base-evaluation.md.txt", "docs__sft-evaluation.md.txt"]
    for name in names:
        shutil.copy2(ROOT / "assets/sources" / name, sources / name)
    # The source snapshot has extra maintenance metadata. Publish only fields
    # rendered by app.js, preserving every displayed score and original output.
    data_path = PUBLIC / 'assets/teaching-data.js'
    full = json.JSONDecoder().raw_decode(data_path.read_text().split('=', 1)[1].lstrip())[0]
    visible_case_fields = ['id','title','query','queryZh','goldPageIds',
                           'goldCandidateIndices','pages','variants','review',
                           'lesson','selectionReason']
    compact = {'metrics': {'rows': full['metrics']['rows']},
               'sftExample': {key: full['sftExample'][key]
                              for key in ['messages','pages','targetRanking']},
               'cases': [{key: row[key] for key in visible_case_fields if key in row}
                         for row in full['cases']],
               'code': full['code']}
    data_path.write_text('// Public classroom evidence; displayed values are unchanged.\n'
                         'window.CLASS_DATA = ' + json.dumps(compact, ensure_ascii=False, indent=2) + ';\n')
    # Keep the provenance useful for students without unpublished source paths,
    # preparation notes or unrelated historical asset bookkeeping.
    evidence = {
        "description": "Saved Base/SFT classroom evidence; no new model calls",
        "evaluationQueries": 1658,
        "trainingExamples": 7200,
        "results": ["ColQwen2 retrieval", "Base Qwen", "SFT Qwen"],
        "files": {str(p.relative_to(PUBLIC)): digest(p)
                  for p in sorted((PUBLIC / "assets").rglob("*")) if p.is_file()},
    }
    (PUBLIC / "assets/provenance.json").write_text(json.dumps(evidence, indent=2) + "\n")
    # This snapshot is tracked with the site source commit. Never read the
    # concurrently changing training checkout while building a deployment.
    snapshot = ROOT / '.openai/project-code.zip'
    if not snapshot.exists():
        raise FileNotFoundError('Run checks/export_code.py before committing a release')
    shutil.copy2(snapshot, PUBLIC / 'project-code.zip')
    updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # Include diagrams, page evidence and downloads, so an asset-only update
    # also notifies students who already have the lesson open.
    release_input = ''.join(str(p.relative_to(PUBLIC)) + digest(p)
                            for p in sorted(PUBLIC.rglob('*')) if p.is_file())
    release = hashlib.sha256((release_input + json.dumps(config, sort_keys=True)).encode()).hexdigest()[:12]
    info = {"version": release, "updatedAt": updated, "githubUrl": url,
            "publicUrl": config.get("publicUrl"), "codeDownload": "project-code.zip"}
    (ROOT / "site-info.js").write_text(
        "// Generated release metadata; edit site-config.json, then rebuild.\n"
        "window.CLASS_SITE = " + json.dumps(info, ensure_ascii=False) + ";\n")
    shutil.copy2(ROOT / "site-info.js", PUBLIC / "site-info.js")
    (PUBLIC / "version.json").write_text(json.dumps(info, indent=2) + "\n")
    # Keep index.html as the stable public entrypoint. Only its dependencies
    # get content-based names. Offline copies retain their original filenames.
    html = (PUBLIC / "index.html").read_text()
    for path in sorted(PUBLIC.rglob("*")):
        if path.is_file() and path.suffix in {".js", ".css"}:
            relative = str(path.relative_to(PUBLIC))
            renamed = path.with_name(path.stem + "." + digest(path)[:12] + path.suffix)
            path.rename(renamed)
            html = html.replace('"' + relative + '"', '"' + str(renamed.relative_to(PUBLIC)) + '"')
    (PUBLIC / "index.html").write_text(html)
    # Helpful to static hosts that honor _headers; hashed scripts already
    # handle cache invalidation on hosts that don't use this convention.
    (PUBLIC / "_headers").write_text(
        "/\n  Cache-Control: no-cache\n"
        "/index.html\n  Cache-Control: no-cache\n"
        "/version.json\n  Cache-Control: no-store\n")
    assert not any(p.name.startswith(".env") for p in PUBLIC.rglob("*"))
    print(json.dumps({"status": "built", "directory": str(PUBLIC), "version": release,
                      "files": sum(p.is_file() for p in PUBLIC.rglob("*")),
                      "githubConfigured": bool(url)}, indent=2))


if __name__ == "__main__":
    build()
