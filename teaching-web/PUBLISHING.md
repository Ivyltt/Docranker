# Public classroom and updates

The stable address is configured in `site-config.json`. Deploying a new version
updates this same address. Content-hashed JavaScript and CSS filenames prevent
old scripts from being reused. Existing browser tabs check for a release once
per minute and offer **Load latest**; they do not reset exercises automatically.

## Edit and publish

1. Edit the bilingual lesson files and verify factual changes against the code.
   For this completed experiment, run `python3 checks/update_results.py` in the
   course checkout to validate saved predictions and refresh the same 1,658-query
   comparison. It performs no inference, training or teacher API calls.
2. Set `githubUrl` in `site-config.json` to the real **project repository**, not a
   profile, example repository or historical reference. The button is hidden
   until this is configured.
3. Run `python3 checks/export_code.py` to freeze the current core project code, then
   `python3 checks/build_public.py`. The generated `dist/` directory is the
   deployable static site. It excludes tests, screenshots, internal notes,
   credentials, model weights and training datasets.
4. Run the browser check and review the diagrams. Commit and push the source
   to this site's managed repository. Save a Sites version using that exact
   commit SHA and a tar archive containing `.openai/hosting.json` plus `dist/`.
5. Deploy the saved version and wait for deployment status `succeeded`.

The publishing assistant should reuse `.openai/hosting.json`'s `project_id`.
Create no replacement project for a routine update. The managed Sites source
repository and the research project's GitHub repository are separate.

## Files to edit

| File | Purpose |
| --- | --- |
| `foundation-content.js` | RAG, dataset, retrieval, labels and evaluation |
| `design-content.js` | Encoder comparison and teacher-supervision explanation |
| `lesson-content.js` | SFT, LoRA, GRPO principles and runnable project commands |
| `experiment-content.js` | Merged-model comparison and the completed GRPO512 run |
| `app.js` | Navigation, bilingual state and manual arithmetic exercises |
| `styles.css` | Classroom, mobile and presentation layouts |
| `site-config.json` | Stable public URL and actual GitHub repository URL |
| `assets/teaching-data.js` | Reviewed saved results and page mappings |
| `checks/build_public.py` | Public allowlist and cache-versioned build |

Do not replace real saved outputs with illustrative examples. Student SFT still
receives real candidate images; only the teacher's refinement request is text-only.
