import json
import os
import base64
import requests
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from llama_index.core import Document
from llama_index.core.schema import TextNode
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    PictureDescriptionApiOptions,
)
from docling_core.types.doc import (
    ImageRefMode,
    PictureItem,
)
from docling_core.types.doc import DoclingDocument

from docling.datamodel.layout_model_specs import DOCLING_LAYOUT_HERON_101
import fitz  # PyMuPDF for PDF text & rendering
import yaml


class DocumentSectionRemover:
    """
    Uses LLM to identify and remove unwanted document sections like Acknowledgments,
    References, Footnotes, Author's name and affiliations.
    """

    # Allowed types you can tweak to your domain
    ALLOWED_SECTION_TYPES = [
        "header",
        "abstract",
        "introduction",
        "methods",
        "materials",
        "results",
        "discussion",
        "authors",
        "affiliations",
        "conclusion",
        "related work",
        "background",
        "references",
        "acknowledgments",
        "appendix",
        "footnotes",
        "toc",
        "body",
        "other",
    ]

    def __init__(
        self,
        ollama_base_url,
        llm_model,
        vision_model,
        enable_llm_cleaning,
    ):
        """
        Initialize the DocumentSectionRemover.

        Args:
            ollama_base_url: Base URL for Ollama API
            llm_model: LLM model to use for section identification
            enable_llm_cleaning: Whether to enable LLM-based section removal
        """
        self.ollama_base_url = ollama_base_url
        self.llm_model = llm_model
        self.enable_llm_cleaning = enable_llm_cleaning
        self.vision_model = vision_model

    def _call_llm_api(self, prompt: str, max_tokens: int = 2000) -> str:
        """
        Use local Ollama.
        Normalizes the output to a plain string.
        """

        try:
            url = f"{self.ollama_base_url}/api/chat"
            headers = {"Content-Type": "application/json"}
            payload = {
                "model": self.llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    # # Ollama uses num_predict instead of max_tokens
                    "num_predict": max_tokens,
                },
            }
            r = requests.post(url, headers=headers, json=payload, timeout=10000)
            r.raise_for_status()
            j = r.json()
            # Native chat response: {"message":{"role":"assistant","content":"..."}}
            if (
                "message" in j
                and isinstance(j["message"], dict)
                and "content" in j["message"]
            ):
                return j["message"]["content"].strip()
        except Exception as e:
            # Helpful diagnostics
            try:
                print(f"ℹ️ /api/chat failed: {e}; body={r.text[:500]}")
            except Exception:
                print(f"ℹ️ /api/chat failed: {e}")

        return ""

    def _ollama_chat_vision(
        self,
        messages,
        num_predict: int = 512,
        temperature: float = 0.1,
        timeout: int = 10000,
    ) -> str:
        """
        Same as _ollama_chat but lets you pick a different model (vision).
        """
        if not self.vision_model:
            print("⚠️ Vision model not configured.")
            return ""
        try:
            url = f"{self.ollama_base_url}/api/chat"
            headers = {"Content-Type": "application/json"}
            payload = {
                "model": self.vision_model,
                "messages": messages,  # include "images": ["data:image/png;base64,..."]
                "stream": False,
                "options": {"temperature": temperature, "num_predict": num_predict},
            }
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            r.raise_for_status()
            j = r.json()
            return (j.get("message", {}) or {}).get("content", "").strip()
        except Exception as e:
            try:
                print(f"ℹ️ /api/chat (vision) failed: {e}; body={r.text[:500]}")
            except Exception:
                print(f"ℹ️ /api/chat (vision) failed: {e}")
            return ""

    # ---------------------------------------
    # NEW: Page-by-page section classification
    # ---------------------------------------
    def _extract_pages_text(
        self, pdf_path: str, max_pages: Optional[int] = None
    ) -> List[str]:
        if not fitz:
            raise RuntimeError("PyMuPDF (fitz) is required for PDF text extraction.")
        doc = fitz.open(pdf_path)
        pages = []
        for i, p in enumerate(doc):
            if max_pages is not None and i >= max_pages:
                break
            pages.append(p.get_text("text") or "")
        return pages

    def classify_sections_per_page(
        self, pdf_path: str, max_pages: Optional[int] = None
    ) -> List[Dict]:
        """
        Runs a TEXT LLM page-by-page.
        Returns a list of items:
          {
            "page_index": int,
            "section_name": str,
            "section_type": str (in ALLOWED_SECTION_TYPES),
            "first_sentence": str,
            "last_sentence": str
          }
        Uses the last item from the previous page as context (name/type).
        """
        pages_text = self._extract_pages_text(pdf_path, max_pages=max_pages)
        results = []
        prev_name, prev_type = "", ""

        for idx, page_text in enumerate(pages_text):
            allowed = json.dumps(self.ALLOWED_SECTION_TYPES)
            prompt = f"""
            You are a careful, literal section labeler for academic PDFs. Label sections for ONE page only.

            Allowed section_type values (lowercase):
            {allowed}

            Rules:
            - Return ONLY a JSON array; no prose.
            - Identify sections appearing on THIS PAGE. If a section continues from the previous page, reuse the same section_name and section_type unless a new heading clearly begins.
            -Do NOT invent headings. If no heading is visible, reuse previous section name (if continuing) or infer a generic name (e.g., "Body").
            - For each section on THIS PAGE, include:
            {{
                "section_name": "<exact heading if present, else inferred name like 'Introduction' or 'Body'>",
                "section_type": "<one of the allowed values>",
                "first_sentence": "<the first sentence of that section on THIS page>",
                "last_sentence": "<the last sentence of that section on THIS page>"
            }}

            Previous page context:
            - prev_section_name: "{prev_name}"
            - prev_section_type: "{prev_type}"

            Page index: {idx}
            Page text:
            \"\"\"{page_text}\"
            \"\"\"
            Return ONLY JSON.
            """
            resp = self._call_llm_api(prompt)
            # Robust JSON extraction
            try:
                s = resp.strip()
                arr = json.loads(
                    s
                    if s.startswith("[")
                    else re.search(r"\[\s*{.*}\s*\]", s, re.DOTALL).group(0)
                )
            except Exception:
                arr = []

            # sanitize & carry forward memory
            for k, item in enumerate(arr):
                name = (item.get("section_name") or "").strip()
                typ = (item.get("section_type") or "").strip().lower()
                if typ not in self.ALLOWED_SECTION_TYPES:
                    typ = "other"
                first_sentence = (item.get("first_sentence") or "").strip()
                last_sentence = (item.get("last_sentence") or "").strip()

                # Update memory to last item on this page
                prev_name = name or prev_name or "Body"
                prev_type = typ or prev_type or "body"

                results.append(
                    {
                        "page_index": idx,
                        "section_name": name or prev_name,
                        "section_type": typ or prev_type,
                        "first_sentence": first_sentence,
                        "last_sentence": last_sentence,
                    }
                )

        return results

    # ---------------------------------------
    # NEW: Vision pass for footnotes per page
    # ---------------------------------------
    def _page_to_b64_image(self, pdf_path: str, page_index: int, dpi: int = 180) -> str:
        """
        Return RAW base64 of the PNG image (NO data: prefix).
        """
        if not fitz:
            raise RuntimeError("PyMuPDF (fitz) is required for PDF rendering.")
        doc = fitz.open(pdf_path)
        page = doc[page_index]
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("png")
        # raw base64 (ascii) – NO "data:image/png;base64," prefix
        b64 = base64.b64encode(img_bytes).decode("ascii")

        try:
            base64.b64decode(b64, validate=True)
        except Exception as e:
            raise ValueError(f"Base64 validation failed for page {page_index}: {e}")

        return b64

    def save_b64img(self, b64img: str, filename: str):

        with open(filename, "wb") as f:
            f.write(base64.b64decode(b64img))

    def detect_footnotes_per_page(
        self, pdf_path: str, max_pages: Optional[int] = None
    ) -> List[Dict]:
        if not self.vision_model:
            print(
                "⚠️ Vision model not set. Set self.vision_model (e.g., 'llava:latest')."
            )
            return []
        if not fitz:
            raise RuntimeError("PyMuPDF (fitz) is required for PDF rendering.")

        doc = fitz.open(pdf_path)
        out = []
        page_range = (
            range(len(doc)) if max_pages is None else range(min(max_pages, len(doc)))
        )

        for i in page_range:
            b64img = self._page_to_b64_image(pdf_path, i)  # RAW base64 string

            # self.save_b64img(b64img, f"page_{i}.png")

            prompt = (
                "You are given a single PDF page as an image.\n"
                "Your task: detect **only footnotes** on THIS page.\n\n"
                "Guidelines:\n"
                "- Footnotes usually appear at the bottom margin of the page.\n"
                "- They are typically in smaller font and may contain superscript markers (¹, ², a, b, *).\n"
                "- Do NOT include main body text, references, or headers.\n\n"
                "Output:\n"
                "Return ONLY a valid JSON array. One object per footnote block:\n"
                "[\n"
                "  {\n"
                '    "section_name": "footnotes",\n'
                '    "section_type": "footnotes",\n'
                '    "first_sentence": "<first sentence of this footnote block>",\n'
                '    "last_sentence": "<last sentence of this footnote block>"\n'
                "  }\n"
                "]\n\n"
                "If there are no footnotes on this page, return [].\n"
                "Do not include explanations, prose, or any text outside the JSON array."
            )

            messages = [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [b64img],  # RAW base64 ONLY
                }
            ]

            resp = self._ollama_chat_vision(messages, num_predict=512)
            try:
                s = resp.strip()
                arr = json.loads(
                    s
                    if s.startswith("[")
                    else re.search(r"\[\s*{.*}\s*\]", s, re.DOTALL).group(0)
                )
            except Exception:
                arr = []

            for item in arr:
                out.append(
                    {
                        "page_index": i,
                        "section_name": (
                            item.get("section_name") or "Footnotes"
                        ).strip(),
                        "section_type": "footnotes",
                        "first_sentence": (item.get("first_sentence") or "").strip(),
                        "last_sentence": (item.get("last_sentence") or "").strip(),
                    }
                )

        return out


class DoclingProcessor:
    """
    Optimized document processor using DoclingReader for both JSON export and content processing.

    Key improvements:
    1. Uses DoclingReader(export_type=JSON) directly - no duplicate processing
    2. Processes document once, gets both structured JSON and clean content
    3. More efficient and cleaner code
    4. Better error handling and fallback options
    5. LLM-based section removal for cleaner content
    """

    def __init__(
        self,
        config_path: str | Path,
        ollama_base_url: str = "http://localhost:11434",
        docling_transformations: List = None,
        embed_model_name=None,
        cache_dir: str | Path = "markdown_documents",
    ):
        """
        Initialize the optimized DoclingProcessor.

        Args:
            enable_vlm_descriptions: Whether to generate VLM descriptions for images
            vlm_model: VLM model to use for image descriptions
            vlm_prompt: Prompt for VLM image descriptions
            ollama_base_url: Base URL for Ollama API
            llm_model: LLM model to use for section identification
            docling_transformations: List of docling transformations (e.g., HybridChunker)
        """
        self.config_path = Path(config_path)
        self.cache_dir = Path(cache_dir)
        self.config = self._load_yaml()
        self.enable_vlm_descriptions = self.config["processing"]["docling"][
            "enable_vlm_descriptions"
        ]
        self.vlm_model = self.config["models"]["vlm"]["model"]
        self.vlm_prompt = self.config["models"]["vlm"]["prompt"]
        self.ollama_base_url = ollama_base_url
        self.enable_section_removal = self.config["processing"]["docling"][
            "enable_section_removal"
        ]
        self.llm_model = self.config["models"]["llm"]["model"]
        self.docling_transformations = docling_transformations or []

        # Initialize chunker if provided
        self.chunker = None
        if self.docling_transformations:
            from endorag.ingestion.transformations import get_transformations

            transformations = get_transformations(self.docling_transformations)
            # Assume first transformation is the chunker
            if transformations:
                self.chunker = transformations[0]
                print(f"✅ Initialized chunker: {type(self.chunker).__name__}")

        # Initialize section remover
        self.section_remover = DocumentSectionRemover(
            ollama_base_url=self.ollama_base_url,
            llm_model=self.llm_model,
            vision_model=self.vlm_model,
            enable_llm_cleaning=self.enable_section_removal,
        )

        # Set up DocumentConverter for image extraction (when VLM is enabled)
        self.converter = None
        pdf_opts = PdfPipelineOptions()
        pdf_opts.layout_options.model_spec = DOCLING_LAYOUT_HERON_101
        pdf_opts.do_ocr = self.config["processing"]["docling"]["enable_ocr"]
        # Force full-page OCR to avoid fragmented paragraphs from native
        # PDF text extraction on web-to-PDF or multi-column documents.
        if pdf_opts.do_ocr:
            from docling.datamodel.pipeline_options import EasyOcrOptions
            force_ocr = self.config["processing"]["docling"].get(
                "force_full_page_ocr", False
            )
            pdf_opts.ocr_options = EasyOcrOptions(
                force_full_page_ocr=force_ocr,
                lang=["en"],
            )
        if self.enable_vlm_descriptions:
            try:
                pdf_opts.images_scale = self.config["processing"]["docling"][
                    "images_scale"
                ]
                pdf_opts.generate_page_images = True
                pdf_opts.generate_picture_images = True

                # Configure VLM if enabled
                pdf_opts.enable_remote_services = True
                pdf_opts.do_picture_description = True
                pdf_opts.picture_description_options = PictureDescriptionApiOptions(
                    url=f"{ollama_base_url}/v1/chat/completions",
                    params={"model": self.vlm_model},
                    prompt=self.vlm_prompt,
                    timeout=10000,
                )
                print(
                    "✅ DocumentConverter initialized for image extraction and VLM descriptions"
                )
            except Exception as e:
                print(f"⚠️  DocumentConverter setup failed: {e}")
                print("🔄 Continuing without VLM descriptions...")

        self.converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_opts)}
        )

    def process_document(self, file_path: str) -> Tuple[str, object]:
        """
        Process a single document using DocumentConverter from Docling

        Args:
            file_path: Path to the document file

        Returns:
            Tuple of (processed_content, docling_document)
        """
        # Define the output Markdown file path using the base name of the PDF
        file_path = Path(file_path)
        base_name = file_path.stem
        output_dir = self.cache_dir / str(self.llm_model) / str(self.vlm_model) / base_name
        output_dir.mkdir(parents=True, exist_ok=True)
        output_md_path = output_dir / f"{base_name}-with-images-descriptions.md"
        output_json_path = output_dir / f"{base_name}-cleaned.json"
        cleaned_output_path = output_dir / f"{base_name}-cleaned.md"
        expanded_output_path = output_dir / f"{base_name}-abbreviations-expanded.md"
        abbreviations_path = output_dir / f"{base_name}.abbreviations.json"

        if os.path.exists(output_md_path):
            print(f"💾 Skipping document: {file_path.name} (already processed)")
            with open(output_md_path, "r") as file:
                md_content = file.read()
            # Expand abbreviations in the loaded markdown
            # md_content = self._expand_abbreviations(md_content, abbreviations_path)
            # with open(expanded_output_path, "w") as file:
            #     file.write(md_content)
            # print(f"💾 Saved abbreviation-expanded markdown: {expanded_output_path}")
            if os.path.exists(output_json_path):
                # Load the docling document if we need chunking
                if self.chunker and os.path.exists(output_json_path):
                    print(f"💾 Skipping document: {file_path.name} (already processed)")
                    print("CHUNKER")
                    with open(output_json_path, "r") as f:
                        doc_dict = json.load(f)

                    doc = DoclingDocument.model_validate(doc_dict)
                    return md_content, doc
                return md_content, None

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        print(f"📄 Processing document: {file_path.name}")

        # Convert the PDF to a structured document object
        result = self.converter.convert(file_path)
        doc = result.document

        # Save the document as Markdown, inserting a custom placeholder for each image.
        doc.save_as_markdown(
            output_md_path,
            image_mode=ImageRefMode.REFERENCED,
        )

        # Read the generated Markdown file content
        with open(output_md_path, "r") as file:
            md_content = file.read()

        # Apply section removal if enabled
        if self.enable_section_removal:
            print("🧹 Removing unwanted document sections...")
            # 1) Page-by-page sections with previous-section context (text)
            sections_json = self.section_remover.classify_sections_per_page(file_path)
            # 2) Footnotes via vision model
            footnotes_json = self.section_remover.detect_footnotes_per_page(file_path)
            # 3) Combine sections and footnotes
            sections_json = sections_json + footnotes_json

            # 4) Remove unwanted sections from markdown content
            md_content = self._remove_unwanted_sections(md_content, sections_json)

            # Save cleaned version
            with open(cleaned_output_path, "w") as file:
                file.write(md_content)
            print(f"💾 Saved cleaned document: {cleaned_output_path}")

        # Expand abbreviations in the freshly generated markdown
        # md_content = self._expand_abbreviations(md_content, abbreviations_path)
        # with open(expanded_output_path, "w") as file:
        #     file.write(md_content)
        # print(f"💾 Saved abbreviation-expanded markdown: {expanded_output_path}")

        doc.save_as_json(
            output_json_path,
        )
        with open(output_json_path, "r") as f:
            doc_dict = json.load(f)
        doc = DoclingDocument.model_validate(doc_dict)
        print(f"Successfully processed: {file_path.name}")

        return md_content, doc

    def process_directory(
        self, source_dir: str, file_extensions: List[str] = None
    ) -> Tuple[List[Document], Optional[List[TextNode]]]:
        """
        Process all documents in a directory using optimized approach.

        Args:
            source_dir: Path to the source directory
            file_extensions: List of file extensions to process (default: ['.pdf'])

        Returns:
            Tuple of (documents, nodes):
            - documents: List of LlamaIndex Document objects (one per file)
            - nodes: List of TextNode objects if HybridChunker is used, None otherwise
        """
        if file_extensions is None:
            file_extensions = [".pdf"]

        source_path = Path(source_dir)
        if not source_path.exists():
            raise FileNotFoundError(f"Source directory not found: {source_dir}")

        documents = []
        nodes = [] if self.chunker else None
        processing_stats = {
            "total_files": 0,
            "successful": 0,
            "failed": 0,
        }

        # Find all files with specified extensions
        for ext in file_extensions:
            # Ensure deterministic ingestion order across runs.
            for file_path in sorted(source_path.rglob(f"*{ext}")):
                processing_stats["total_files"] += 1

                try:
                    print(f"\n📁 Processing: {file_path.relative_to(source_path)}")
                    # Process the document
                    processed_content, docling_doc = self.process_document(file_path)

                    # Always create ONE Document per file
                    doc = Document(
                        text=processed_content,
                        metadata={
                            "file_name": file_path.name,
                            "file_path": str(file_path),
                            "source_type": "docling_processor",
                            "processing_method": "DoclingProcessor with section removal",
                            "content_length": len(processed_content),
                        },
                        exclude_embed_metadata_keys=["file_path", "source_type", "processing_method", "content_length"],
                        excluded_llm_metadata_keys=["file_path", "source_type", "processing_method", "content_length"],
                        metadata_seperator="\n",
                        metadata_template="{key}: {value}",
                        text_template="Metadata:\n{metadata_str}\n-----\nContent:\n{content}",
                    )
                    documents.append(doc)

                    # Apply chunking if chunker is available - create Nodes
                    if self.chunker and docling_doc:
                        print(
                            f"✂️  Applying HybridChunker to document: {file_path.name}"
                        )
                        try:
                            # Apply the chunker to the docling document
                            chunk_iter = self.chunker.chunk(dl_doc=docling_doc)

                            chunk_count = 0
                            for chunk in chunk_iter:
                                # Use contextualize() to add headers and context
                                # This significantly improves retrieval quality!
                                try:
                                    enriched_text = self.chunker.contextualize(
                                        chunk=chunk
                                    )
                                except Exception as ctx_error:
                                    # Fallback to plain text if contextualize fails
                                    print(
                                        f"⚠️  Contextualization failed, using plain text: {ctx_error}"
                                    )
                                    enriched_text = chunk.text

                                # Create TextNode for each chunk with enriched text
                                # ref_doc_id must be passed in constructor, not set after
                                node = TextNode(
                                    text=enriched_text,
                                    metadata={
                                        "file_name": file_path.name,
                                        "file_path": str(file_path),
                                        "source_type": "docling_processor",
                                        "processing_method": "DoclingProcessor with HybridChunker (contextualized)",
                                        "chunk_index": chunk_count,
                                        "content_length": len(enriched_text),
                                    },
                                    ref_doc_id=doc.doc_id,
                                )
                                nodes.append(node)
                                chunk_count += 1

                            print(
                                f"✅ Created {chunk_count} contextualized nodes for: {file_path.name}"
                            )
                        except Exception as chunk_error:
                            print(f"⚠️  Chunking failed: {chunk_error}")
                            # If chunking fails, nodes will be None and pipeline will handle it

                    processing_stats["successful"] += 1
                    print(f"✅ Successfully processed: {file_path.name}")

                except Exception as e:
                    processing_stats["failed"] += 1
                    print(f"❌ Error processing {file_path}: {e}")
                    continue

        # Calculate final statistics (no content reduction tracking in this version)

        print(f"\n📊 Processing Summary:")
        print(f"   📁 Total files: {processing_stats['total_files']}")
        print(f"   ✅ Successful: {processing_stats['successful']}")
        print(f"   ❌ Failed: {processing_stats['failed']}")
        if nodes:
            print(f"   ✂️  Total nodes (chunks): {len(nodes)}")

        return documents, nodes

    def _remove_unwanted_sections(
        self, md_content: str, sections_json: List[Dict]
    ) -> str:
        """
        Remove unwanted sections from markdown content based on sections_json.

        Args:
            md_content: The markdown content to clean
            sections_json: List of section dictionaries with first_sentence and last_sentence

        Returns:
            Cleaned markdown content with unwanted sections removed
        """
        # Define sections to remove (case-insensitive)
        sections_to_remove = [
            "authors",
            "affiliations",
            "references",
            "acknowledgments",
            "footnotes",
        ]

        # Track removed content for debugging
        removed_sections = []
        original_length = len(md_content)

        # Sort sections by their position in the document (find first sentence position)
        # Process from end to beginning to avoid position shifts after removal
        sections_with_positions = []
        for section in sections_json:
            section_type = section.get("section_type", "").lower().strip()
            section_name = section.get("section_name", "").strip()
            first_sentence = section.get("first_sentence", "").strip()[:50]

            # Check if this section should be removed
            if any(remove_term in section_type for remove_term in sections_to_remove):
                if first_sentence:
                    pos = md_content.find(first_sentence)
                    if pos != -1:
                        sections_with_positions.append((pos, section))

        # Sort by position (reverse order - from end to beginning)
        sections_with_positions.sort(key=lambda x: x[0], reverse=True)

        # Process each section that should be removed
        for pos, section in sections_with_positions:
            section_type = section.get("section_type", "")
            section_name = section.get("section_name", "").strip()
            first_sentence = section.get("first_sentence", "").strip()[:100]
            last_sentence = section.get("last_sentence", "").strip()[-100:]

            if first_sentence and last_sentence:
                # Find the text between first and last sentence (inclusive)
                print(f"Extracting text between sentences with header: {section_name}")
                print(f"First sentence: {first_sentence}")
                print(f"Last sentence: {last_sentence}")
                removed_text = self._extract_text_between_sentences_with_header(
                    md_content, section_name, first_sentence, last_sentence
                )

                if removed_text:
                    # Remove the text from content
                    md_content = md_content.replace(removed_text, "", 1)
                    removed_sections.append(
                        {
                            "section_type": section_type,
                            "section_name": section_name,
                            "removed_text_length": len(removed_text),
                            "first_sentence": (
                                first_sentence + "..."
                                if len(first_sentence) > 50
                                else first_sentence
                            ),
                            "last_sentence": (
                                last_sentence + "..."
                                if len(last_sentence) > 50
                                else last_sentence
                            ),
                        }
                    )
                    print(
                        f"🗑️  Removed section '{section_type}' with header '{section_name}' ({len(removed_text)} chars)"
                    )
                else:
                    print(
                        f"⚠️  Could not find text to remove for section '{section_type}'"
                    )

        # Clean up extra whitespace and newlines
        md_content = re.sub(
            r"\n\s*\n\s*\n", "\n\n", md_content
        )  # Multiple newlines -> double newline
        md_content = md_content.strip()

        final_length = len(md_content)
        reduction = original_length - final_length

        if removed_sections:
            print(
                f"✅ Removed {len(removed_sections)} unwanted sections ({reduction} chars total)"
            )
        else:
            print("ℹ️  No unwanted sections found to remove")

        return md_content

    def _extract_text_between_sentences(
        self, text: str, first_sentence: str, last_sentence: str
    ) -> str:
        """
        Extract text between first and last sentence (inclusive).

        Args:
            text: The full text to search in
            first_sentence: The first sentence to start from
            last_sentence: The last sentence to end at

        Returns:
            The text between and including the sentences, or empty string if not found
        """
        if not first_sentence or not last_sentence:
            return ""

        # Try exact match first
        first_pos = text.find(first_sentence)
        if first_pos == -1:
            # Try partial match (first 30 characters of the sentence)
            first_partial = first_sentence[:30]
            first_pos = text.find(first_partial)
            if first_pos == -1:
                return ""

        # Find the position of the last sentence, starting from first_pos
        last_pos = text.find(last_sentence, first_pos)
        if last_pos == -1:
            # Try partial match for last sentence too
            last_partial = last_sentence[:30]
            last_pos = text.find(last_partial, first_pos)
            if last_pos == -1:
                # If we can't find the last sentence, try to find it from the end
                last_pos = text.rfind(last_sentence)
                if last_pos == -1:
                    last_pos = text.rfind(last_sentence[:30])
                if last_pos == -1 or last_pos < first_pos:
                    return ""

        # Extract text from first sentence start to last sentence end
        # Use the actual last sentence for length calculation
        if text[last_pos : last_pos + len(last_sentence)] == last_sentence:
            last_sentence_end = last_pos + len(last_sentence)
        else:
            # If using partial match, find the end of the sentence
            sentence_end = text.find(".", last_pos)
            if sentence_end == -1:
                sentence_end = text.find("\n", last_pos)
            if sentence_end == -1:
                last_sentence_end = last_pos + len(last_sentence[:30])
            else:
                last_sentence_end = sentence_end + 1

        extracted_text = text[first_pos:last_sentence_end]

        # Extend to include any trailing whitespace or newlines that are part of the section
        while last_sentence_end < len(text) and text[last_sentence_end] in " \t\n":
            last_sentence_end += 1

        return text[first_pos:last_sentence_end]

    def _extract_text_between_sentences_with_header(
        self, text: str, section_name: str, first_sentence: str, last_sentence: str
    ) -> str:
        """
        Extract text between first and last sentence (inclusive), including the section header.

        Args:
            text: The full text to search in
            section_name: The section name/header to look for
            first_sentence: The first sentence to start from
            last_sentence: The last sentence to end at

        Returns:
            The text between and including the sentences with the header, or empty string if not found
        """
        if not first_sentence or not last_sentence:
            return ""

        # First, try to find the section header
        header_patterns = []
        if section_name:
            # Try different markdown header formats
            header_patterns = [
                f"## {section_name.upper()}",  # ## ACKNOWLEDGMENTS
                f"# {section_name.upper()}",  # # ACKNOWLEDGMENTS
                f"### {section_name.upper()}",  # ### ACKNOWLEDGMENTS
                f"## {section_name}",  # ## Acknowledgments
                f"# {section_name}",  # # Acknowledgments
                f"### {section_name}",  # ### Acknowledgments
                f"**{section_name.upper()}**",  # **ACKNOWLEDGMENTS**
                f"**{section_name}**",  # **Acknowledgments**
                section_name.upper(),  # ACKNOWLEDGMENTS (plain text)
                section_name,  # Acknowledgments (plain text)
            ]

        # Find the section header position
        header_pos = -1
        found_header = ""
        for pattern in header_patterns:
            header_pos = text.find(pattern)
            if header_pos != -1:
                found_header = pattern
                print(f"Found header pattern: '{pattern}' at position {header_pos}")
                break

        # Find the first sentence position
        first_pos = text.find(first_sentence)
        if first_pos == -1:
            # Try partial match (first 30 characters of the sentence)
            first_partial = first_sentence[:30]
            first_pos = text.find(first_partial)
            if first_pos == -1:
                return ""

        # Determine the start position: use header if found and it's before first sentence
        start_pos = first_pos
        if header_pos != -1 and header_pos < first_pos:
            # Check if header is reasonably close to first sentence (within 500 chars)
            if first_pos - header_pos < 500:
                start_pos = header_pos
                print(
                    f"Using header position {header_pos} instead of first sentence position {first_pos}"
                )

        # Find the position of the last sentence, starting from first_pos
        last_pos = text.find(last_sentence, first_pos)
        if last_pos == -1:
            # Try partial match for last sentence too
            last_partial = last_sentence[:30]
            last_pos = text.find(last_partial, first_pos)
            if last_pos == -1:
                # If we can't find the last sentence, try to find it from the end
                last_pos = text.rfind(last_sentence)
                if last_pos == -1:
                    last_pos = text.rfind(last_sentence[:30])
                if last_pos == -1 or last_pos < first_pos:
                    return ""

        # Extract text from start position to last sentence end
        # Use the actual last sentence for length calculation
        if text[last_pos : last_pos + len(last_sentence)] == last_sentence:
            last_sentence_end = last_pos + len(last_sentence)
        else:
            # If using partial match, find the end of the sentence
            sentence_end = text.find(".", last_pos)
            if sentence_end == -1:
                sentence_end = text.find("\n", last_pos)
            if sentence_end == -1:
                last_sentence_end = last_pos + len(last_sentence[:30])
            else:
                last_sentence_end = sentence_end + 1

        # Extend to include any trailing whitespace or newlines that are part of the section
        while last_sentence_end < len(text) and text[last_sentence_end] in " \t\n":
            last_sentence_end += 1

        # Also include any leading whitespace before the header
        while start_pos > 0 and text[start_pos - 1] in " \t\n":
            start_pos -= 1

        extracted_text = text[start_pos:last_sentence_end]
        print(
            f"Extracted text from {start_pos} to {last_sentence_end} (length: {len(extracted_text)})"
        )

        return extracted_text

    def _expand_abbreviations(self, md_content: str, abbreviations_path: str) -> str:
        """
        Replace abbreviations in markdown content with 'Long Form (ABBR)'.

        Only whole-word occurrences are replaced.  Abbreviations are processed
        longest-first so that e.g. 'DIO2' is handled before 'DIO'.

        Args:
            md_content:          The markdown text to transform.
            abbreviations_path:  Path to the ``*.abbreviations.json`` file.

        Returns:
            The markdown content with abbreviations expanded.
        """
        if not os.path.exists(abbreviations_path):
            return md_content

        try:
            with open(abbreviations_path, "r") as f:
                abbr_map: Dict[str, str] = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️  Could not load abbreviations file {abbreviations_path}: {e}")
            return md_content

        if not abbr_map:
            return md_content

        # Sort by length (longest first) to avoid partial-match collisions
        sorted_abbrs = sorted(abbr_map.keys(), key=len, reverse=True)

        for abbr in sorted_abbrs:
            long_form = abbr_map[abbr]
            # Build a pattern that matches the abbreviation as a whole word,
            # but NOT when it is already followed by ' (Long Form)' or
            # preceded by 'Long Form (' – i.e. skip already-expanded text.
            # \b on both sides ensures we don't match inside longer words.
            pattern = re.compile(
                r"(?<!\w)" + re.escape(abbr) + r"(?!\w)"
            )
            replacement = f"{long_form} ({abbr})"
            md_content = pattern.sub(replacement, md_content)

        print(f"✅ Expanded {len(abbr_map)} abbreviations in document")
        return md_content

    def _load_yaml(self) -> Dict[str, any]:
        """Loads and parses the YAML configuration file."""
        with open(self.config_path, "r") as file:
            return yaml.safe_load(file)
