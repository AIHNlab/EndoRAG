==============================================================================
POINT 1 — Failure modes of the agentic workflow (EndoRAG), model=gemma4:31b-cloud
==============================================================================

Aligned questions: 386   Wrong (EndoRAG): 46   Accuracy: 0.8808

Per-dataset (EndoRAG acc | rerank-RAG acc | LLM-only acc | wrong):
  Diabetes      0.849 | 0.830 | 0.642 |  8/53
  Thyroid       0.932 | 0.847 | 0.797 |  4/59
  Parathyroid   0.953 | 0.907 | 0.930 |  2/43
  Pituitary     0.926 | 0.815 | 0.722 |  4/54
  Adrenal       0.880 | 0.860 | 0.720 |  6/50
  Reproductive  0.857 | 0.762 | 0.810 |  6/42
  UKEU          0.812 | 0.729 | 0.624 | 16/85

Failure overlap:
  EndoRAG wrong & LLM-only wrong ......... 33
  EndoRAG wrong & rerank-RAG wrong ....... 33
  EndoRAG, LLM-only & rerank-RAG wrong ... 28
  EndoRAG-only wrong (others correct) ... 8

Automatic failure signals over the wrong set (non-exclusive):
  Incorrect domain routing (routed != dataset domain) . 4
  Insufficient evidence (answerability flag / follow-up) 19
  Retrieval gap vs best single-pass rerank RAG ........ 13
  Residual reasoning error (rel. evidence, no gap) .... 19

Decision source among WRONG cases:
  agreement                    29
  arbiter:agentic              15
  arbiter:baseline_selector    2
  final answer == internal baseline selector: 31/46

Decision source over ALL cases:
  agreement                    321
  arbiter:agentic              56
  arbiter:baseline_selector    9

==============================================================================
POINT 2 — Are agentic==LLM-only/RAG ties a sign of contamination?
==============================================================================

### Model: minimax-m2.7:cloud
dataset       agentic   norr  rerank   llm ag=base  ag=rr  all4=
Diabetes         79.2   75.5    79.2  73.6    84.9   79.2   60.4
Thyroid          91.5   83.1    86.4  76.3    86.4   91.5   74.6
Parathyroid      90.7   90.7    90.7  86.0    95.3   86.0   76.7
Pituitary        85.2   79.6    83.3  79.6    85.2   83.3   66.7
Adrenal          80.0   82.0    82.0  78.0    86.0   94.0   78.0
Reproductive     76.2   71.4    73.8  76.2    97.6   92.9   76.2
UKEU             75.3   65.9    70.6  63.5    83.5   69.4   48.2

### Model: gemma4:31b-cloud
dataset       agentic   norr  rerank   llm ag=base  ag=rr  all4=
Diabetes         84.9   77.4    83.0  64.2    83.0   84.9   50.9
Thyroid          93.2   84.7    84.7  79.7    88.1   91.5   67.8
Parathyroid      95.3   90.7    90.7  93.0    81.4   90.7   83.7
Pituitary        92.6   68.5    81.5  72.2    88.9   87.0   59.3
Adrenal          88.0   82.0    86.0  72.0    86.0   86.0   68.0
Reproductive     85.7   73.8    76.2  81.0    85.7   83.3   64.3
UKEU             81.2   68.2    72.9  62.4    84.7   76.5   51.8

### Model: nemotron-3-nano:30b-cloud
dataset       agentic   norr  rerank   llm ag=base  ag=rr  all4=
Diabetes         84.9   75.5    79.2  64.2    96.2   86.8   52.8
Thyroid          86.4   83.1    88.1  69.5    86.4   86.4   62.7
Parathyroid      88.4   88.4    86.0  79.1    95.3   83.7   67.4
Pituitary        74.1   68.5    72.2  57.4    83.3   79.6   51.9
Adrenal          78.0   78.0    76.0  62.0    76.0   76.0   56.0
Reproductive     69.0   66.7    66.7  64.3    88.1   78.6   59.5
UKEU             58.8   57.6    62.4  48.2    81.2   63.5   35.3

### Model: mistral-small3.2:24b
dataset       agentic   norr  rerank   llm ag=base  ag=rr  all4=
Diabetes         73.6    NA     64.2  43.4    83.0   66.0    0.0
Thyroid          88.1   81.4    83.1  64.4    88.1   84.7   55.9
Parathyroid      90.7   90.7    83.7  74.4    95.3   90.7   67.4
Pituitary        63.0   68.5    75.9  51.9    87.0   75.9   53.7
Adrenal          80.0   78.0    78.0  52.0    86.0   84.0   50.0
Reproductive     69.0   61.9    64.3  61.9    85.7   61.9   50.0
UKEU             57.6   49.4    51.8  38.8    74.1   55.3   29.4

--- Contamination scan: minimax-m2.7:cloud / Reproductive ---
  distinct retrieval sources: 51
  answer-key/question-bank-like sources: NONE
    - AdrenalLiterature/Endocrinology and Diabetes Book.pdf
    - AdrenalLiterature/Greenspan’s Basic and Clinical Endocrinology.pdf
    - AdrenalLiterature/The adrenal gland - Endocrinology - NCBI Bookshelf.pdf
    - AdrenalLiterature/williams-textbook-of-endocrinology-12th-edition.pdf
    - ParathyroidLiterature/Endocrinology and Diabetes Book.pdf
    - ParathyroidLiterature/Greenspan’s Basic and Clinical Endocrinology.pdf
    - PituitaryLiterature/Approach to the Patient With Prolactinoma.pdf
    - PituitaryLiterature/Chapter-10---Anterior-Pituitary-Failure_2011_The-Pituitary.pdf
    - PituitaryLiterature/Chapter-7---Gonadotropin-Hormones_2011_The-Pituitary.pdf
    - PituitaryLiterature/Clinical Endocrinology - 2006 - Casanueva - Guidelines of the Pituitary Society for the diagnosis and management of.pdf
    - PituitaryLiterature/Endocrinology and Diabetes Book.pdf
    - PituitaryLiterature/Greenspan’s Basic and Clinical Endocrinology.pdf
    - PituitaryLiterature/Hypogonadotropic Hypogonadism Revisited.pdf
    - PituitaryLiterature/Prolactinoma Management - Endotext - NCBI Bookshelf.pdf
    - PituitaryLiterature/williams-textbook-of-endocrinology-12th-edition.pdf
    - ReproductiveEndocrinologyLiterature/A combined analysis of data to identify predictive factors for spermatogenesis in men with hypogonadotropic hypogonadism treated with recombinant human follicle-stimulating hormone and human chorionic gonadotropin.pdf
    - ReproductiveEndocrinologyLiterature/A practical guide to male hypogonadism in the primary care setting.pdf
    - ReproductiveEndocrinologyLiterature/Amenorrhea - StatPearls - NCBI Bookshelf.pdf
    - ReproductiveEndocrinologyLiterature/Amenorrhea. A Systematic Approach to Diagnosis and Management.pdf
    - ReproductiveEndocrinologyLiterature/American Association of Clinical Endocrinologists Medical Guidelines for clinical practice for the evaluation and treatment of hypogonadism in adult male patients--2002 update.pdf
    - ReproductiveEndocrinologyLiterature/An update on male hypogonadism therapy.pdf
    - ReproductiveEndocrinologyLiterature/Anabolic steroid–induced hypogonadism. diagnosis and treatment.pdf
    - ReproductiveEndocrinologyLiterature/Androgen deficiency in older men. Indications, advantages, and pitfalls of testosterone replacement therapy.pdf
    - ReproductiveEndocrinologyLiterature/Approach to the Patient. Low Testosterone Concentrations in Men with Obesity.pdf
    - ReproductiveEndocrinologyLiterature/Causes of male infertility - UpToDate.pdf
    - ReproductiveEndocrinologyLiterature/Clinical Assessment and Mutation Analysis of Kallmann Syndrome 1 (KAL1) and Fibroblast Growth Factor Receptor 1 (FGFR1, or KAL2) in Five Families and 18 Sporadic Patients.pdf
    - ReproductiveEndocrinologyLiterature/Clinical Endocrinology - 2025 - Elhassan - Society for Endocrinology Clinical Practice Guideline for the Evaluation of.pdf
    - ReproductiveEndocrinologyLiterature/Clinical approach to the male with delayed puberty.pdf
    - ReproductiveEndocrinologyLiterature/Current_evaluation_of_amenorrhea.pdf
    - ReproductiveEndocrinologyLiterature/Delayed Puberty.pdf
    - ReproductiveEndocrinologyLiterature/Diagnosis and Management of Anabolic Androgenic Steroid Use.pdf
    - ReproductiveEndocrinologyLiterature/EAU-Guidelines-on-Sexual-and-Reproductive-Health-2025.pdf
    - ReproductiveEndocrinologyLiterature/Endocrinology and Diabetes Book.pdf
    - ReproductiveEndocrinologyLiterature/Endocrinology of Pregnancy - Endotext - NCBI Bookshelf.pdf
    - ReproductiveEndocrinologyLiterature/Focus Issue on Male Infertility.pdf
    - ReproductiveEndocrinologyLiterature/Functional Hypothalamic Amenorrhea.pdf
    - ReproductiveEndocrinologyLiterature/Ganong's Review of Medical Physiology.pdf
    - ReproductiveEndocrinologyLiterature/Greenspan’s Basic and Clinical Endocrinology.pdf
    - ReproductiveEndocrinologyLiterature/Hypogonadotropic Hypogonadism Revisited.pdf
    - ReproductiveEndocrinologyLiterature/Interpretation of Basic Semen Analysis and Advanced Semen Testing.pdf
    - ReproductiveEndocrinologyLiterature/Mumps orchitis.pdf
    - ReproductiveEndocrinologyLiterature/Ovarian hyperthecosis - UpToDate.pdf
    - ReproductiveEndocrinologyLiterature/Practical Diabetes International - 2010 - Dhatariya - ABCD position statement on the management of hypogonadal males with.pdf
    - ReproductiveEndocrinologyLiterature/Predicting the menopause the role of inhibin B.pdf
    - ReproductiveEndocrinologyLiterature/Secondary Amenorrhea - StatPearls - NCBI Bookshelf.pdf
    - ReproductiveEndocrinologyLiterature/Updated ultrasound criteria for polycystic ovary syndrome. reliable thesholds for elevated follicle population and ovarian volume.pdf
    - ReproductiveEndocrinologyLiterature/Volume 5, Chapter 21. Amenorrhea.pdf
    - ReproductiveEndocrinologyLiterature/What is the optimal therapy for young males with hypogonadotropic hypogonadism.pdf
    - ReproductiveEndocrinologyLiterature/williams-textbook-of-endocrinology-12th-edition.pdf
    - ThyroidLiterature/Greenspan’s Basic and Clinical Endocrinology.pdf
    - ThyroidLiterature/williams-textbook-of-endocrinology-12th-edition.pdf

  Accuracy  LLM-only=0.762  no-rerank RAG=0.714  rerank RAG=0.738  agentic=0.762
  aligned questions: 42
  agentic==LLM-only==rerankRAG (same letter): 34/42
    ...of those, all correct: 30
  NOTE: run_diabetes_workflow(input_text, deps) receives ONLY the question; expected_output is attached later for scoring (see evaluate_exam.py).
