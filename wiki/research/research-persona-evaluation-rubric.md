---
type: research
confidence: high
produced_by_thread: true
related_entities: []
source_ids: [src_niu778_internal_mimir, src_niu778_mt_bench, src_niu778_position_bias, src_niu778_alpacaeval_length, src_niu778_benchmark_contamination]
---

# Evaluation Rubric for Claude vs Codex Research Personas

> **TL;DR** — Compare research personas with blinded pairwise review on the same prompt, score mostly for factual grounding and usefulness, track cost and process hygiene separately, and rerun a small 6-task benchmark each week with a larger hidden set monthly.

## Compiled Truth

### What A Good Research Run Looks Like

- Produces a Mimir page that answers the user request directly, not just a notes dump.
- Uses credible sources, prefers primary sources, and distinguishes internal repo evidence from external evidence.
- Makes claims traceable with inline citations and minimal unsupported assertions.
- Synthesizes material into decisions, tradeoffs, or guidance that would save the next operator time.
- Fits Mimir conventions: clear title, TL;DR, `## Compiled Truth`, optional `## Timeline`, and no obvious duplication with existing pages (`wiki/research/best-practices-agent-mimir-pages.md` and `src/mimir/FORMAT.md`).
- Shows calibration: confidence should drop when evidence is thin, conflicting, or time-sensitive.

### Recommended Rubric

Score each run on a 1-5 anchored scale per dimension, then compute a weighted total out of 100.

| Dimension | Weight | What judges score |
|---|---:|---|
| Factual grounding and source quality | 30 | Are the important claims correct, well-supported, current enough, and based on credible sources? |
| Usefulness for Niuu | 25 | Would this page help a teammate make a better decision or move faster right now? |
| Structure and page quality | 15 | Is the page easy to scan, logically organized, and faithful to Mimir format? |
| Synthesis and novelty | 15 | Does it connect sources into non-obvious conclusions instead of merely summarizing them? |
| Research process hygiene | 15 | Did it search existing Mimir, avoid duplication, cite clearly, and show appropriate confidence? |

Use these anchors for all five dimensions:

| Score | Anchor |
|---|---|
| 5 | Excellent: clearly decision-ready, well-supported, and hard to improve materially |
| 4 | Strong: useful and reliable, with minor gaps |
| 3 | Adequate: mostly correct but missing depth, clarity, or prioritization |
| 2 | Weak: noticeable omissions, thin support, or limited practical value |
| 1 | Poor: misleading, poorly grounded, or not useful |

### Human-Judged Vs Automatic Metrics

Human judgment should decide the headline quality score because page usefulness, synthesis quality, and judgment under uncertainty are not reliably reducible to counts alone (Zheng et al., 2023; Shi et al., 2025).

**Human-judged dimensions**

- Factual grounding and source quality
- Usefulness for Niuu
- Structure and page quality
- Synthesis and novelty
- Overall pairwise preference: `A better`, `B better`, or `Tie`

**Automatically measurable diagnostics**

- Time to first source consulted
- Total wall-clock runtime
- Tokens or cost, if available from the harness
- Number of cited sources
- Source diversity: internal pages, primary external sources, and distinct domains
- Citation coverage: cited claims or cited sections per 100 words
- Broken links or missing citations
- Output length and Mimir format compliance

Automatic metrics should be treated as guardrails and efficiency diagnostics, not as the winner selection rule. They are easy to game and can reward verbosity unless controlled (Dubois et al., 2024).

### How To Compare Two Runs Fairly

- Use the same prompt, tool access, time budget, and stopping rule for both personas.
- Blind the outputs before review. Remove persona names and normalize superficial formatting where possible.
- Present pages in randomized order and re-run a swapped-order comparison on a sample of tasks because LLM and human judges can show position bias (Shi et al., 2025).
- Ask judges to score by rubric first, then record a pairwise winner. This reduces style-only preferences.
- Allow ties. Many prompt pairs will be practically equivalent.
- Keep efficiency separate from quality. Report `quality winner` and `efficiency winner` independently.
- Use at least two human raters for a subset of the benchmark and track agreement. If agreement is low, fix rubric wording before drawing model conclusions.

### Minimal Repeated Benchmark

Start with 6 prompts that cover the main failure modes of this environment:

| Task type | What it tests |
|---|---|
| Internal-only synthesis | Finds and consolidates existing repo/Mimir knowledge without duplication |
| Internal architecture explanation | Correct repo reading, structure, and relevance filtering |
| External current-state research | Source quality, recency handling, and citation discipline |
| Mixed internal plus external recommendation | Ability to combine repo context with outside evidence into a practical recommendation |
| Conflict-resolution prompt | Handles ambiguous or conflicting evidence without false certainty |
| Distillation prompt | Converts a broad question into a concise, decision-ready Mimir page |

Keep 4 prompts visible and stable for week-to-week tracking. Rotate 2 hidden prompts monthly to reduce benchmark overfitting and contamination risk (Choi et al., 2025).

### Recommended Operating Cadence

- Weekly smoke test: run the 6-task benchmark on current Claude and Codex personas.
- Monthly deeper comparison: add 12-18 rotated prompts and include one rerun for stability.
- For weekly runs, use one human reviewer for all tasks and a second reviewer on 2 tasks chosen at random.
- For monthly runs, use two human reviewers on all tasks and record agreement plus swapped-order audits on at least 25% of comparisons.
- Publish one scorecard per run: weighted quality score, pairwise win rate excluding ties, tie rate, runtime, cost, and notable failure modes.
- Prefer trend lines over single-run winners. A model change should be called better only if it improves pairwise win rate and does not regress usefulness or grounding on the rotated set.

## Timeline

- 2026-05-05: Reviewed existing Mimir page conventions and confirmed no existing research page covered Claude-versus-Codex research persona evaluation. [Source: repo wiki and Mimir format docs, 2026-05-05]
- 2026-05-05: Added a practical rubric centered on blinded pairwise review, weighted human scoring, automatic diagnostics, and a 6-task recurring benchmark. [Source: NIU-778 task plus external evaluation references, 2026-05-05]

## Sources

- Internal Mimir conventions: `wiki/research/best-practices-agent-mimir-pages.md` and `src/mimir/FORMAT.md` (repo, retrieved 2026-05-05)
- Zheng et al., "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena" (arXiv, retrieved 2026-05-05): https://arxiv.org/abs/2306.05685
- Shi et al., "Judging the Judges: A Systematic Study of Position Bias in LLM-as-a-Judge" (arXiv, retrieved 2026-05-05): https://arxiv.org/abs/2406.07791
- Dubois et al., "Length-Controlled AlpacaEval: A Simple Way to Debias Automatic Evaluators" (COLM 2024 / OpenReview PDF, retrieved 2026-05-05): https://openreview.net/pdf/76a4afefba676b543f4c4ca61529f92e828e171f.pdf
- Choi et al., "How Contaminated Is Your Benchmark? Measuring Dataset Leakage in Large Language Models with Kernel Divergence" (PMLR, retrieved 2026-05-05): https://proceedings.mlr.press/v267/choi25b.html

<!-- sources: src_niu778_internal_mimir, src_niu778_mt_bench, src_niu778_position_bias, src_niu778_alpacaeval_length, src_niu778_benchmark_contamination -->
