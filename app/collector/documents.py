"""Pick the job-description files attached to a posting and read their text."""

from __future__ import annotations

import io
import logging
import re
import zipfile

from pypdf import PdfReader
from pypdf.errors import PdfReadError

# Malformed attachments make pypdf log noisy but harmless warnings
logging.getLogger("pypdf").setLevel(logging.ERROR)

READABLE_EXTENSIONS = (".pdf", ".docx")
MAX_DOCUMENT_BYTES = 8_000_000
MAX_DOCUMENT_CHARS = 15_000
MAX_DOCUMENTS_PER_POSTING = 2
JD_NAME_PATTERN = re.compile(
    r"\b(jd|job|description|role|profile|position|opening|hiring|requirement|intern|"
    r"engineer|developer|analyst|trainee)\b|jd",
    re.IGNORECASE,
)
# Generic placement-cell files that are attached to many postings but say nothing about the role
GENERIC_NAME_PATTERN = re.compile(
    r"policy|declaration|guide|consent|undertaking|form|sample|template|malpractice|"
    r"instruction|rules|code of conduct|nda|agreement",
    re.IGNORECASE,
)


def is_likely_jd(name: str) -> bool:
    """True for PDF/Word files whose name suggests a job description."""
    lowered = name.lower()
    if not lowered.endswith(READABLE_EXTENSIONS):
        return False
    stem = re.sub(r"[_\-.]+", " ", lowered.rsplit(".", 1)[0])
    return bool(JD_NAME_PATTERN.search(stem)) and not GENERIC_NAME_PATTERN.search(stem)


def document_text(name: str, content: bytes) -> str:
    """Plain text of a PDF or Word file; empty when it cannot be read."""
    if len(content) > MAX_DOCUMENT_BYTES:
        return ""
    if name.lower().endswith(".pdf"):
        text = _pdf_text(content)
    elif name.lower().endswith(".docx"):
        text = _docx_text(content)
    else:
        text = ""
    return re.sub(r"[ \t]+", " ", text).strip()[:MAX_DOCUMENT_CHARS]


def _pdf_text(content: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages[:10])
    except (PdfReadError, ValueError, KeyError):
        return ""


def _docx_text(content: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError):
        return ""
    paragraphs = re.split(r"</w:p>", xml)
    return "\n".join(re.sub(r"<[^>]+>", "", paragraph) for paragraph in paragraphs)
