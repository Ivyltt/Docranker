# Recorded test results

All models use the same 1,658 test questions, original candidate images, complete gold labels, and evaluation definition.

| Model | Macro Recall@1 | MRR | nDCG@5 | Format fallback |
| --- | ---: | ---: | ---: | ---: |
| Base | 61.0927% | 0.758313 | 0.767047 | 1607/1658 |
| SFT900 merged | 63.8892% | 0.786460 | 0.786846 | 0/1658 |
| SFT + GRPO512 | 65.7187% | 0.797376 | 0.794628 | 0/1658 |

Paired GRPO-minus-merged-SFT differences:

| Metric | Difference (percentage points) | 95% paired interval (percentage points) |
| --- | ---: | ---: |
| Macro Recall@1 | +1.8295 | [+0.9643, +2.8247] |
| MRR | +1.0917 | [+0.5911, +1.6749] |
| nDCG@5 | +0.7781 | [+0.4111, +1.1731] |

Macro Recall@1 averages each question's retrieved-positive count divided by its complete gold-page count. For multiple-positive questions, it differs from whether the first page is relevant. First-position hit rates are 71.3510% (1183/1658) for merged SFT and 73.3414% (1216/1658) for GRPO.

Intervals resample paired questions 1,000 times with seed 42; they do not include training-seed variability or document clustering. The checkpoint was selected using a validation rule declared before testing.

The GRPO run completed 768 updates. The selected checkpoint is step 512: 512 freshly sampled groups, 2,048 generated completions, and 173 groups with different ranking rewards.

For SFT supervision, Gemini first reads one real page image and its gold label, then refines the page notes using text only. The Qwen student always receives the question and actual candidate images. GRPO ranking rewards use training gold labels.
