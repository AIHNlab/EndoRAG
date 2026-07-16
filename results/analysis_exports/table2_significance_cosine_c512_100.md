# Table 2 Pairwise Significance

Exact two-sided McNemar tests were computed from raw per-question correctness vectors for the planned comparison of Single-pass RAG with re-ranking against Agentic Workflow. Two-sided paired t-tests were also computed on the per-question correctness differences. When a raw file is partial, paired tests use the overlapping questions and report the paired sample size.

Per-dataset planned tests: 28
Per-dataset McNemar significant at alpha=0.05: 2
Per-dataset paired t-test significant at alpha=0.05: 3
Pooled all-datasets tests: 4
Pooled McNemar significant at alpha=0.05: 1
Pooled paired t-test significant at alpha=0.05: 1

## Per-Dataset Comparisons

| LLM | Dataset | Comparison | Paired n | Delta pp | McNemar p | McNemar sig. | Paired t | Paired t p | t-test sig. | Discordant (A>B / B>A) |
| --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: | --- | ---: |
| gemma4:31b-cloud | Diabetes | Single-pass RAG w/ rerank -> Agentic Workflow | 53 | 1.89 | 1 | no | 0.374887 | 0.709269 | no | 3 / 4 |
| gemma4:31b-cloud | Thyroid | Single-pass RAG w/ rerank -> Agentic Workflow | 59 | 8.47 | 0.0625 | no | 2.31741 | 0.0240318 | yes | 0 / 5 |
| gemma4:31b-cloud | Parathyroid | Single-pass RAG w/ rerank -> Agentic Workflow | 43 | 4.65 | 0.625 | no | 1 | 0.323037 | no | 1 / 3 |
| gemma4:31b-cloud | Pituitary | Single-pass RAG w/ rerank -> Agentic Workflow | 54 | 11.11 | 0.03125 | yes | 2.57391 | 0.0128876 | yes | 0 / 6 |
| gemma4:31b-cloud | Adrenal | Single-pass RAG w/ rerank -> Agentic Workflow | 50 | 2.00 | 1 | no | 0.374701 | 0.709499 | no | 3 / 4 |
| gemma4:31b-cloud | Reproductive | Single-pass RAG w/ rerank -> Agentic Workflow | 42 | 9.52 | 0.21875 | no | 1.66723 | 0.10309 | no | 1 / 5 |
| gemma4:31b-cloud | UKEU | Single-pass RAG w/ rerank -> Agentic Workflow | 85 | 8.24 | 0.143463 | no | 1.7171 | 0.0896464 | no | 5 / 12 |
| nemotron-3-nano:30b | Diabetes | Single-pass RAG w/ rerank -> Agentic Workflow | 53 | 5.66 | 0.453125 | no | 1.13702 | 0.260742 | no | 2 / 5 |
| nemotron-3-nano:30b | Thyroid | Single-pass RAG w/ rerank -> Agentic Workflow | 59 | -1.69 | 1 | no | -0.375202 | 0.708879 | no | 4 / 3 |
| nemotron-3-nano:30b | Parathyroid | Single-pass RAG w/ rerank -> Agentic Workflow | 43 | 2.33 | 1 | no | 0.374166 | 0.710163 | no | 3 / 4 |
| nemotron-3-nano:30b | Pituitary | Single-pass RAG w/ rerank -> Agentic Workflow | 54 | 1.85 | 1 | no | 0.330573 | 0.742271 | no | 4 / 5 |
| nemotron-3-nano:30b | Adrenal | Single-pass RAG w/ rerank -> Agentic Workflow | 50 | 2.00 | 1 | no | 0.298753 | 0.766392 | no | 5 / 6 |
| nemotron-3-nano:30b | Reproductive | Single-pass RAG w/ rerank -> Agentic Workflow | 42 | 2.38 | 1 | no | 0.442913 | 0.660157 | no | 2 / 3 |
| nemotron-3-nano:30b | UKEU | Single-pass RAG w/ rerank -> Agentic Workflow | 85 | -3.53 | 0.690038 | no | -0.597727 | 0.55163 | no | 14 / 11 |
| mistral-small3.2:24b | Diabetes | Single-pass RAG w/ rerank -> Agentic Workflow | 53 | 9.43 | 0.266846 | no | 1.39923 | 0.167682 | no | 4 / 9 |
| mistral-small3.2:24b | Thyroid | Single-pass RAG w/ rerank -> Agentic Workflow | 59 | 5.08 | 0.453125 | no | 1.1367 | 0.26034 | no | 2 / 5 |
| mistral-small3.2:24b | Parathyroid | Single-pass RAG w/ rerank -> Agentic Workflow | 43 | 6.98 | 0.25 | no | 1.77482 | 0.083175 | no | 0 / 3 |
| mistral-small3.2:24b | Pituitary | Single-pass RAG w/ rerank -> Agentic Workflow | 54 | -12.96 | 0.015625 | yes | -2.80956 | 0.00693377 | yes | 7 / 0 |
| mistral-small3.2:24b | Adrenal | Single-pass RAG w/ rerank -> Agentic Workflow | 50 | 2.00 | 1 | no | 0.374701 | 0.709499 | no | 3 / 4 |
| mistral-small3.2:24b | Reproductive | Single-pass RAG w/ rerank -> Agentic Workflow | 42 | 4.76 | 0.774414 | no | 0.572713 | 0.569967 | no | 5 / 7 |
| mistral-small3.2:24b | UKEU | Single-pass RAG w/ rerank -> Agentic Workflow | 85 | 5.88 | 0.38331 | no | 1.09233 | 0.277812 | no | 8 / 13 |
| minimax-m2.7:cloud | Diabetes | Single-pass RAG w/ rerank -> Agentic Workflow | 53 | 0.00 | 1 | no | 0 | 1 | no | 5 / 5 |
| minimax-m2.7:cloud | Thyroid | Single-pass RAG w/ rerank -> Agentic Workflow | 59 | 5.08 | 0.25 | no | 1.76271 | 0.0832179 | no | 0 / 3 |
| minimax-m2.7:cloud | Parathyroid | Single-pass RAG w/ rerank -> Agentic Workflow | 43 | 0.00 | 1 | no | 0 | 1 | no | 3 / 3 |
| minimax-m2.7:cloud | Pituitary | Single-pass RAG w/ rerank -> Agentic Workflow | 54 | 1.85 | 1 | no | 0.374945 | 0.709198 | no | 3 / 4 |
| minimax-m2.7:cloud | Adrenal | Single-pass RAG w/ rerank -> Agentic Workflow | 50 | -2.00 | 1 | no | -0.573462 | 0.568954 | no | 2 / 1 |
| minimax-m2.7:cloud | Reproductive | Single-pass RAG w/ rerank -> Agentic Workflow | 42 | 2.38 | 1 | no | 1 | 0.323176 | no | 0 / 1 |
| minimax-m2.7:cloud | UKEU | Single-pass RAG w/ rerank -> Agentic Workflow | 85 | 4.71 | 0.541256 | no | 0.814881 | 0.417445 | no | 10 / 14 |

## Pooled All-Datasets Comparisons

| LLM | Paired n | Delta pp | McNemar p | McNemar sig. | Paired t | Paired t p | t-test sig. | Discordant (A>B / B>A) |
| --- | ---: | ---: | ---: | --- | ---: | ---: | --- | ---: |
| gemma4:31b-cloud | 386 | 6.74 | 0.000409541 | yes | 3.66309 | 0.000284068 | yes | 13 / 39 |
| nemotron-3-nano:30b | 386 | 0.78 | 0.812589 | no | 0.355631 | 0.722311 | no | 34 / 37 |
| mistral-small3.2:24b | 386 | 3.11 | 0.188216 | no | 1.43625 | 0.151744 | no | 29 / 41 |
| minimax-m2.7:cloud | 386 | 2.07 | 0.340891 | no | 1.08892 | 0.276869 | no | 23 / 31 |

The companion CSV includes source file paths for every test.
