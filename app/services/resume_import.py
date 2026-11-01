"""Read a resume PDF and turn it into profile fields and portfolio projects to review."""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.services.branches import find_branch
from app.services.jd_parser import find_cgpa, find_degree, find_technologies
from app.services.llm import LlmClient

logger = logging.getLogger(__name__)

MAX_PDF_BYTES = 5_000_000
MAX_PAGES = 6
MAX_TEXT_CHARS = 15_000
MIN_READABLE_CHARS = 200
MAX_PROJECTS = 8
MAX_LIST_ITEMS = 30
SHORT_TERM_LENGTH = 3
YEAR_PATTERN = re.compile(r"\b(20[1-4]\d)\b")
URL_PATTERN = re.compile(r"https?://[^\s)>\]]+|(?:github\.com|gitlab\.com)/[^\s)>\]]+")

RESUME_SYSTEM = """You read a student's resume text and return structured facts from it.

Hard rules:
- Copy facts from the resume only. Never add skills, numbers, links or projects it does not contain.
- Use null or an empty list when the resume does not say.
- Skills are general abilities and languages; technologies are tools, frameworks and databases.

Respond with a JSON object:
{"degree": "e.g. B.Tech or null", "cgpa": 8.4, "graduation_year": 2027,
 "skills": ["..."], "technologies": ["..."], "interests": ["..."], "locations": ["..."],
 "projects": [{"name": "...", "description": "one line in the resume's words",
               "technologies": ["..."], "link": "url or null"}]}"""


class ResumeReadError(ValueError):
    """The upload is not a readable resume; the message is safe to show the student."""


@dataclass
class ProjectDraft:
    name: str
    description: str = ""
    technologies: list[str] = field(default_factory=list)
    link: str | None = None


@dataclass
class ResumeProfile:
    degree: str | None = None
    branch: str | None = None
    cgpa: float | None = None
    graduation_year: int | None = None
    skills: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)
    interests: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    projects: list[ProjectDraft] = field(default_factory=list)
    used_ai: bool = False
    dropped: list[str] = field(default_factory=list)


def extract_text(pdf_bytes: bytes) -> str:
    """Plain text of the first pages of a PDF; raises ResumeReadError when unreadable."""
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise ResumeReadError(
            "That file is larger than 5 MB. Please upload a smaller PDF."
        )
    if not pdf_bytes.startswith(b"%PDF"):
        raise ResumeReadError(
            "That file is not a PDF. Please upload your resume as a PDF."
        )
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = reader.pages[:MAX_PAGES]
        text = "\n".join(page.extract_text() or "" for page in pages)
    except (PdfReadError, ValueError, KeyError) as exc:
        raise ResumeReadError(
            "This PDF could not be opened. Try exporting it again."
        ) from exc
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) < MIN_READABLE_CHARS:
        raise ResumeReadError(
            "No text could be read from this PDF. It may be a scanned image; "
            "export it from Word or Google Docs instead."
        )
    return text[:MAX_TEXT_CHARS]


def parse_resume(text: str, llm: LlmClient | None) -> ResumeProfile:
    """Structure resume text, keeping only values that appear in the resume itself."""
    rules = _parse_with_rules(text)
    if llm is None or not llm.configured:
        return rules
    answer = llm.complete_json(RESUME_SYSTEM, text)
    if not answer:
        return rules
    profile = _grounded_profile(answer, text)
    _fill_gaps(profile, rules)
    profile.used_ai = True
    return profile


def _parse_with_rules(text: str) -> ResumeProfile:
    return ResumeProfile(
        degree=find_degree(text),
        branch=find_branch(text),
        cgpa=find_cgpa(text),
        graduation_year=_graduation_year(text),
        technologies=find_technologies(text)[:MAX_LIST_ITEMS],
    )


def _graduation_year(text: str) -> int | None:
    # The latest year on a line that names the degree is the expected graduation year
    for line in text.splitlines():
        if find_degree(line):
            years = [int(year) for year in YEAR_PATTERN.findall(line)]
            if years:
                return max(years)
    return None


def _grounded_profile(answer: dict, text: str) -> ResumeProfile:
    lowered = text.lower()
    profile = ResumeProfile()
    profile.degree = _grounded_text(answer.get("degree"), lowered, profile, "degree")
    profile.cgpa = _grounded_number(answer.get("cgpa"), text, float, profile, "CGPA")
    profile.graduation_year = _grounded_number(
        answer.get("graduation_year"), text, int, profile, "graduation year"
    )
    for name in ("skills", "technologies", "interests", "locations"):
        setattr(profile, name, _grounded_list(answer.get(name), lowered, profile))
    profile.projects = _grounded_projects(answer.get("projects"), text, profile)
    return profile


def _grounded_text(
    value: object, lowered: str, profile: ResumeProfile, label: str
) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if _appears(text, lowered):
        return text
    profile.dropped.append(f"{label}: {text}")
    return None


def _grounded_number(
    value: object, text: str, kind: type, profile: ResumeProfile, label: str
) -> float | int | None:
    if value in (None, ""):
        return None
    try:
        number = kind(value)
    except (TypeError, ValueError):
        return None
    shown = f"{number:g}" if kind is float else str(number)
    if shown in text or str(value) in text:
        return number
    profile.dropped.append(f"{label}: {value}")
    return None


def _grounded_list(values: object, lowered: str, profile: ResumeProfile) -> list[str]:
    kept: list[str] = []
    for value in values if isinstance(values, list) else []:
        text = str(value).strip()
        if not text or text in kept:
            continue
        if _appears(text, lowered):
            kept.append(text)
        else:
            profile.dropped.append(text)
    return kept[:MAX_LIST_ITEMS]


def _grounded_projects(
    values: object, text: str, profile: ResumeProfile
) -> list[ProjectDraft]:
    lowered = text.lower()
    links = {match.rstrip(".,") for match in URL_PATTERN.findall(text)}
    projects: list[ProjectDraft] = []
    for entry in values if isinstance(values, list) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name or not _appears(name, lowered):
            profile.dropped.append(f"project: {name}")
            continue
        link = str(entry.get("link") or "").strip().rstrip(".,")
        projects.append(
            ProjectDraft(
                name=name,
                description=str(entry.get("description") or "").strip()[:300],
                technologies=_grounded_list(
                    entry.get("technologies"), lowered, profile
                ),
                link=_full_url(link) if link in links else None,
            )
        )
    return projects[:MAX_PROJECTS]


def _fill_gaps(profile: ResumeProfile, rules: ResumeProfile) -> None:
    profile.degree = profile.degree or rules.degree
    profile.branch = rules.branch
    profile.cgpa = profile.cgpa if profile.cgpa is not None else rules.cgpa
    profile.graduation_year = profile.graduation_year or rules.graduation_year
    for technology in rules.technologies:
        if technology.lower() not in {
            item.lower() for item in profile.technologies + profile.skills
        }:
            profile.technologies.append(technology)


def _appears(value: str, lowered_text: str) -> bool:
    squashed = _squash(value)
    if not squashed:
        return False
    # Short names like "C" or "Go" would match inside any word, so they need a whole word
    if len(squashed) <= SHORT_TERM_LENGTH:
        pattern = r"(?<![\w+#])" + re.escape(value.lower().strip()) + r"(?![\w+#])"
        return re.search(pattern, lowered_text) is not None
    return squashed in _squash(lowered_text)


def _squash(text: str) -> str:
    # Compare without punctuation so "B.Tech" matches "BTech" and "Node.js" matches "NodeJS"
    return re.sub(r"[^a-z0-9+#]", "", text.lower())


def _full_url(link: str) -> str:
    return link if link.startswith("http") else f"https://{link}"
