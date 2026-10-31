"""Deterministic JD parser.

Extracts the facts an agent needs before planning: skills, likely rounds, bond
terms, pay, location, work type, eligibility and the deadline. It never guesses:
anything the text does not state is returned as ``None`` and listed in
``unknown_fields``.

No model is involved. That keeps extraction reproducible and cheap, and means
the critic can trace every parsed value back to a line in the JD.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.collector.api import html_to_text

# Canonical technology names. Matching is case-insensitive and bounded, so
# "Go" in a sentence does not become the Go language unless it appears alone.
_TECH_VOCABULARY: dict[str, str] = {
    "python": "Python",
    "java": "Java",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "c++": "C++",
    "c#": "C#",
    ".net": ".NET",
    "golang": "Go",
    "rust": "Rust",
    "kotlin": "Kotlin",
    "swift": "Swift",
    "php": "PHP",
    "ruby": "Ruby",
    "scala": "Scala",
    "matlab": "MATLAB",
    "sql": "SQL",
    "mysql": "MySQL",
    "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL",
    "sqlite": "SQLite",
    "oracle": "Oracle",
    "mongodb": "MongoDB",
    "redis": "Redis",
    "elasticsearch": "Elasticsearch",
    "kafka": "Kafka",
    "rabbitmq": "RabbitMQ",
    "spark": "Apache Spark",
    "hadoop": "Hadoop",
    "hive": "Hive",
    "aws": "AWS",
    "azure": "Azure",
    "gcp": "GCP",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "terraform": "Terraform",
    "jenkins": "Jenkins",
    "git": "Git",
    "github": "GitHub",
    "gitlab": "GitLab",
    "linux": "Linux",
    "bash": "Bash",
    "shell scripting": "Shell",
    "django": "Django",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "spring boot": "Spring Boot",
    "spring framework": "Spring",
    "node.js": "Node.js",
    "nodejs": "Node.js",
    "react": "React",
    "angular": "Angular",
    "vue": "Vue",
    "next.js": "Next.js",
    "express.js": "Express",
    "graphql": "GraphQL",
    "rest api": "REST",
    "html": "HTML",
    "css": "CSS",
    "tailwind": "Tailwind CSS",
    "bootstrap": "Bootstrap",
    "pandas": "Pandas",
    "numpy": "NumPy",
    "scikit-learn": "scikit-learn",
    "sklearn": "scikit-learn",
    "tensorflow": "TensorFlow",
    "pytorch": "PyTorch",
    "keras": "Keras",
    "opencv": "OpenCV",
    "nlp": "NLP",
    "machine learning": "Machine Learning",
    "deep learning": "Deep Learning",
    "data structures": "Data Structures",
    "algorithms": "Algorithms",
    "oop": "OOP",
    "dbms": "DBMS",
    "operating systems": "Operating Systems",
    "computer networks": "Computer Networks",
    "system design": "System Design",
    "ms excel": "Excel",
    "tableau": "Tableau",
    "power bi": "Power BI",
    "salesforce": "Salesforce",
    "sap": "SAP",
    "selenium": "Selenium",
    "playwright": "Playwright",
    "pytest": "pytest",
    "junit": "JUnit",
    "agile": "Agile",
    "scrum": "Scrum",
    "jira": "Jira",
}

_HEADING_ALIASES: dict[str, tuple[str, ...]] = {
    "skills": (
        "required skills",
        "skills required",
        "technical skills",
        "skill set",
        "skills",
    ),
    "rounds": (
        "hiring workflow",
        "interview process",
        "selection process",
        "selection procedure",
        "interview rounds",
        "hiring process",
        "rounds",
    ),
    "eligibility": (
        "eligibility criteria",
        "eligibility",
        "qualifications",
        "who can apply",
    ),
    "description": (
        "job description",
        "role description",
        "about the role",
        "job responsibilities",
        "responsibilities",
    ),
    "compensation": (
        "compensation",
        "ctc",
        "salary",
        "package",
        "stipend",
        "pay",
    ),
    "location": ("location", "job location", "work location"),
    "work_type": ("work type", "position type", "employment type", "job type"),
    "experience": ("experience", "experience required", "work experience"),
    "other": (
        "additional questions",
        "benefits",
        "perks",
        "about the company",
        "about us",
        "job functions",
        "other info",
        "opening overview",
        "role",
        "deadline",
        "application deadline",
        "bond",
        "service agreement",
        "notes",
        "how to apply",
    ),
}

_BOND_PATTERN = re.compile(
    r"\bbonds?\b|service agreement|training agreement|minimum service|"
    r"retention agreement",
    re.IGNORECASE,
)
_NO_BOND_MARKERS = (
    "no bond",
    "no service agreement",
    "no training agreement",
    "bond is not applicable",
    "without bond",
)
_ROUND_MARKERS = (
    "round",
    "interview",
    "assessment",
    "aptitude",
    "coding test",
    "online test",
    "group discussion",
    "gd ",
    "hr ",
    "technical test",
    "machine coding",
    "case study",
)
_DEADLINE_MARKERS = (
    "deadline",
    "last date",
    "apply before",
    "apply by",
    "closes on",
    "closing date",
    "applications close",
    "application close",
    "last day to apply",
)
_PAY_MARKERS = (
    "ctc",
    "salary",
    "compensation",
    "stipend",
    "package",
    "pay",
    "remuneration",
    "per annum",
    "per month",
    "lpa",
    "inr",
    "₹",
    "$",
)

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

_DEGREE_PATTERN = re.compile(
    r"\b(B\.?\s?Tech|M\.?\s?Tech|B\.?\s?E|M\.?\s?E|B\.?\s?Sc|M\.?\s?Sc|"
    r"B\.?\s?C\.?\s?A|M\.?\s?C\.?\s?A|MBA|B\.?\s?Com|M\.?\s?Com|Ph\.?\s?D)\b",
    re.IGNORECASE,
)
_DEGREE_CANONICAL = {
    "btech": "B.Tech",
    "mtech": "M.Tech",
    "be": "B.E.",
    "me": "M.E.",
    "bsc": "B.Sc",
    "msc": "M.Sc",
    "bca": "BCA",
    "mca": "MCA",
    "mba": "MBA",
    "bcom": "B.Com",
    "mcom": "M.Com",
    "phd": "PhD",
}

_AMOUNT = r"([\d][\d,]*(?:\.\d+)?)"
_UNIT = r"(lpa|lakhs?|lacs?|crores?|cr|k|thousand)?"


@dataclass
class PayInfo:
    raw: str
    amount: float | None = None
    currency: str | None = None
    unit: str | None = None  # absolute | thousand | lakh | crore
    interval: str | None = None  # annum | month

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "amount": self.amount,
            "currency": self.currency,
            "unit": self.unit,
            "interval": self.interval,
        }


@dataclass
class BondClause:
    applicable: bool
    raw: str
    months: int | None = None
    amount: float | None = None
    currency: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "applicable": self.applicable,
            "raw": self.raw,
            "months": self.months,
            "amount": self.amount,
            "currency": self.currency,
        }


@dataclass
class ParsedJd:
    skills: list[str] | None = None
    rounds: list[str] | None = None
    bond: BondClause | None = None
    pay: PayInfo | None = None
    location: str | None = None
    work_type: str | None = None
    eligibility: str | None = None
    degree: str | None = None
    min_cgpa: float | None = None
    graduation_year: int | None = None
    experience: str | None = None
    deadline: str | None = None
    unknown_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skills": self.skills,
            "rounds": self.rounds,
            "bond": self.bond.to_dict() if self.bond else None,
            "pay": self.pay.to_dict() if self.pay else None,
            "location": self.location,
            "work_type": self.work_type,
            "eligibility": self.eligibility,
            "degree": self.degree,
            "min_cgpa": self.min_cgpa,
            "graduation_year": self.graduation_year,
            "experience": self.experience,
            "deadline": self.deadline,
            "unknown_fields": list(self.unknown_fields),
        }


# -- helpers ------------------------------------------------------------


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(line.strip() for line in text.splitlines())


def _clean_bullet(line: str) -> str:
    line = re.sub(r"^\s*(?:[-*•●▪‣]|\d+[.)])\s*", "", line)
    return " ".join(line.split()).strip(" -–—:;")


def _split_values(value: str) -> list[str]:
    parts = re.split(r"[,;|•●▪‣]|\s{2,}", value)
    return [_clean_bullet(part) for part in parts if _clean_bullet(part)]


def _heading(line: str) -> tuple[str | None, str | None]:
    """Return ``(section, inline_value)`` when a line is a known heading."""
    cleaned = line.strip().rstrip(":").strip()
    lowered = cleaned.lower()
    for section, aliases in _HEADING_ALIASES.items():
        for alias in aliases:
            if lowered == alias:
                return section, None
            if lowered.startswith(alias + ":"):
                return section, cleaned[len(alias) + 1 :].strip() or None
            if lowered.startswith(alias + " -"):
                return section, cleaned[len(alias) + 2 :].strip() or None
    # Unknown "Label: value" lines still end the previous section, so later
    # fields (deadline, bond, batch) never bleed into eligibility prose.
    label = re.match(r"^([A-Za-z][A-Za-z0-9 /&()'.-]{1,40}):\s*(.*)$", cleaned)
    if label:
        return "other", label.group(2).strip() or None
    return None, None


def _sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        section, inline = _heading(line)
        if section is not None:
            current = section
            sections.setdefault(current, [])
            if inline:
                sections[current].append(inline)
            continue
        if current and line:
            sections[current].append(line)
    return sections


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value.strip())
    return result


def _sentence_matching(text: str, pattern: re.Pattern[str]) -> str:
    """Return the first sentence matching a regex (bounded for evidence)."""
    for line in text.splitlines():
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            if pattern.search(sentence):
                return " ".join(sentence.split())[:300]
    return ""


def _to_float(value: str) -> float | None:
    try:
        return float(value.replace(",", ""))
    except (TypeError, ValueError):
        return None


def _unit(value: str | None) -> str | None:
    if not value:
        return None
    value = value.lower()
    if value.startswith("lpa") or value.startswith("lakh") or value.startswith("lac"):
        return "lakh"
    if value.startswith("crore") or value == "cr":
        return "crore"
    if value in ("k", "thousand"):
        return "thousand"
    return None


# -- field parsers ------------------------------------------------------


def _trim_at_next_label(value: str) -> str:
    """Cut an inline value when a later labelled field starts on the same line."""
    return re.split(
        r"[.;]?\s+(?=(?:location|compensation|salary|ctc|eligibility|deadline|bond|"
        r"work\s+type|position\s+type|experience|hiring\s+workflow|job\s+description|"
        r"required\s+skills|additional\s+questions)\s*:)",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]


def _parse_skills(lines: list[str], sections: dict[str, list[str]]) -> list[str] | None:
    found: list[str] = []

    for line in sections.get("skills", []):
        found.extend(_split_values(_trim_at_next_label(line)))

    for line in lines:
        match = re.match(
            r"(?:required\s+|preferred\s+|technical\s+)?skills?\s*[:\-]\s*(.+)",
            line,
            re.IGNORECASE,
        )
        if match:
            found.extend(_split_values(_trim_at_next_label(match.group(1))))

    # Known technologies mentioned anywhere in the JD.
    haystack = "\n".join(lines).lower()
    for term, canonical in _TECH_VOCABULARY.items():
        pattern = r"(?<![\w+#.])" + re.escape(term) + r"(?![\w+#.])"
        if re.search(pattern, haystack, re.IGNORECASE):
            found.append(canonical)

    cleaned = [
        value
        for value in _dedupe(found)
        if value
        and len(value) <= 40
        and "no skills" not in value.lower()
        and not value.lower().startswith("no ")
    ]
    return cleaned[:25] or None


def _parse_rounds(lines: list[str], sections: dict[str, list[str]]) -> list[str] | None:
    rounds: list[str] = []
    for line in sections.get("rounds", []):
        for part in re.split(r"[;\n]", line):
            cleaned = _clean_bullet(part)
            if 2 < len(cleaned) <= 200:
                rounds.append(cleaned)

    if not rounds:
        for line in lines:
            lowered = line.lower()
            heading = _heading(line)[0]
            if (
                any(marker in lowered for marker in _ROUND_MARKERS)
                and 2 < len(_clean_bullet(line)) <= 200
                and heading
                not in (
                    "skills",
                    "rounds",
                    "eligibility",
                    "compensation",
                    "location",
                    "work_type",
                    "experience",
                    "description",
                )
            ):
                rounds.append(_clean_bullet(line))

    return _dedupe(rounds)[:8] or None


def _parse_bond(text: str) -> BondClause | None:
    if not _BOND_PATTERN.search(text):
        return None

    raw = _sentence_matching(text, _BOND_PATTERN)
    if not raw:
        raw = "bond mentioned"

    if any(marker in raw.lower() for marker in _NO_BOND_MARKERS):
        return BondClause(applicable=False, raw=raw)

    months: int | None = None
    month_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:months?|mos?)\b", raw, re.IGNORECASE)
    year_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:years?|yrs?)\b", raw, re.IGNORECASE)
    if month_match:
        months = int(float(month_match.group(1)))
    elif year_match:
        months = int(float(year_match.group(1)) * 12)

    amount: float | None = None
    currency: str | None = None
    amount_match = re.search(
        r"(?:rs\.?|inr|₹)\s*" + _AMOUNT + r"\s*" + _UNIT,
        raw,
        re.IGNORECASE,
    )
    if amount_match:
        amount = _to_float(amount_match.group(1))
        currency = "INR"
    else:
        amount_match = re.search(
            _AMOUNT + r"\s*(lakhs?|lacs?|lpa|crores?|cr|thousand)\b",
            raw,
            re.IGNORECASE,
        )
        if amount_match:
            amount = _to_float(amount_match.group(1))
            currency = "INR"

    return BondClause(
        applicable=True,
        raw=raw,
        months=months,
        amount=amount,
        currency=currency,
    )


def parse_pay(text: str) -> PayInfo | None:
    """Parse the headline pay figure from a compensation line."""
    line = " ".join(str(text).split())
    if not line:
        return None
    lowered = line.lower()

    patterns = (
        r"(?:rs\.?|inr|₹)\s*" + _AMOUNT + r"\s*" + _UNIT,
        _AMOUNT + r"\s*(lpa|lakhs?|lacs?|crores?|cr)\b",
        _AMOUNT + r"\s*(?:inr|rs\.?|₹|rupees)\b",
        r"\$\s*" + _AMOUNT + r"\s*" + _UNIT,
        _AMOUNT + r"\s*(?:per\s+annum|per\s+month|p\.a\.|annually|monthly)\b",
    )

    amount: float | None = None
    unit: str | None = None
    currency: str | None = None
    for pattern in patterns:
        match = re.search(pattern, line, re.IGNORECASE)
        if not match:
            continue
        amount = _to_float(match.group(1))
        unit = _unit(match.group(2) if match.lastindex and match.lastindex >= 2 else None)
        if "usd" in lowered or "$" in match.group(0):
            currency = "USD"
        elif any(token in match.group(0).lower() for token in ("₹", "inr", "rs", "rupee")):
            currency = "INR"
        elif unit in ("lakh", "crore") or "lpa" in lowered:
            currency = "INR"
        break

    if amount is None:
        return None
    if currency is None and any(token in lowered for token in ("₹", "inr", "rs.", "rupees", "lpa")):
        currency = "INR"

    interval: str | None = None
    if any(
        token in lowered
        for token in ("per annum", "p.a.", "annum", "annual", "lpa", "lakh", "lac", "crore")
    ):
        interval = "annum"
    elif any(
        token in lowered
        for token in ("per month", "monthly", "month", "stipend")
    ):
        interval = "month"
    elif any(token in lowered for token in ("per year", "yearly", "annual")):
        interval = "annum"

    return PayInfo(
        raw=line[:300],
        amount=amount,
        currency=currency,
        unit=unit,
        interval=interval,
    )


def _parse_pay(lines: list[str], sections: dict[str, list[str]]) -> PayInfo | None:
    candidates: list[str] = []
    for line in sections.get("compensation", []):
        candidates.append(line)
    for line in lines:
        lowered = line.lower()
        if any(marker in lowered for marker in _PAY_MARKERS):
            candidates.append(line)

    parsed: list[PayInfo] = []
    for candidate in _dedupe(candidates):
        pay = parse_pay(candidate)
        if pay is not None:
            parsed.append(pay)
    if not parsed:
        return None

    # Prefer a line that is explicitly about CTC/salary, then the first figure.
    parsed.sort(
        key=lambda item: (
            not any(token in item.raw.lower() for token in ("ctc", "salary", "package", "stipend")),
            item.raw.lower().find("ctc") == -1,
        )
    )
    return parsed[0]


def _section_value(
    sections: dict[str, list[str]], name: str, limit: int = 160
) -> str | None:
    values = sections.get(name) or []
    for value in values:
        cleaned = " ".join(value.split()).strip()
        if cleaned:
            return cleaned[:limit]
    return None


def _parse_deadline(lines: list[str]) -> str | None:
    for line in lines:
        lowered = line.lower()
        if not any(marker in lowered for marker in _DEADLINE_MARKERS):
            continue
        found = _find_date(line)
        if found:
            return found
    return None


def _find_date(text: str) -> str | None:
    iso = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", text)
    if iso:
        return _valid_date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))

    numeric = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b", text)
    if numeric:
        day, month, year = (int(part) for part in numeric.groups())
        return _valid_date(year, month, day)

    day_month = re.search(
        r"\b(\d{1,2})\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
        r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?)\s*,?\s*(20\d{2})\b",
        text,
        re.IGNORECASE,
    )
    if day_month:
        month = _MONTHS.get(day_month.group(2).lower()[:3])
        if month:
            return _valid_date(int(day_month.group(3)), month, int(day_month.group(1)))

    month_day = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
        r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})\s*,?\s*(20\d{2})\b",
        text,
        re.IGNORECASE,
    )
    if month_day:
        month = _MONTHS.get(month_day.group(1).lower()[:3])
        if month:
            return _valid_date(int(month_day.group(3)), month, int(month_day.group(2)))
    return None


def _valid_date(year: int, month: int, day: int) -> str | None:
    try:
        import datetime as dt

        return dt.date(year, month, day).isoformat()
    except ValueError:
        return None


def _parse_eligibility(
    lines: list[str], sections: dict[str, list[str]]
) -> str | None:
    section = sections.get("eligibility") or []
    relevant = [
        line
        for line in lines
        if _heading(line)[0] != "eligibility"
        and any(
            marker in line.lower()
            for marker in (
                "cgpa",
                "gpa",
                "eligible",
                "b.tech",
                "b.e.",
                "batch",
                "graduat",
                "degree",
            )
        )
    ]
    # Section prose plus explicit labelled lines (e.g. "Batch: 2027") that may
    # sit outside the section but still define eligibility.
    parts = list(section) + relevant
    text = " ".join(" ".join(part.split()) for part in _dedupe(parts))
    return text[:800] or None


def _parse_degree(text: str) -> str | None:
    match = _DEGREE_PATTERN.search(text)
    if not match:
        return None
    raw = re.sub(r"[\s.]+", "", match.group(1)).lower()
    return _DEGREE_CANONICAL.get(raw, match.group(1).upper())


def _parse_cgpa(text: str) -> float | None:
    for pattern in (
        r"(?:min(?:imum)?\s*)?(?:cgpa|gpa)\s*(?:of|:|-)?\s*(\d{1,2}(?:\.\d+)?)",
        r"(\d{1,2}(?:\.\d+)?)\s*(?:cgpa|gpa)\b",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            if 0 <= value <= 10:
                return value
    return None


def _parse_graduation_year(text: str) -> int | None:
    patterns = (
        r"(?:batch|graduat\w*|passing|pass\s*out|year\s*of\s*passing)"
        r"[^\d]{0,20}(20\d{2})",
        r"(20\d{2})\s*(?:batch|passing|graduates?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _parse_experience(lines: list[str], sections: dict[str, list[str]]) -> str | None:
    value = _section_value(sections, "experience")
    if value:
        return value
    for line in lines:
        match = re.match(
            r"(?:experience|work\s+experience|exp)\s*[:\-]\s*(.+)", line, re.IGNORECASE
        )
        if match:
            return match.group(1).strip()[:120]
    return None


def _parse_work_type(
    text: str, sections: dict[str, list[str]], title: str | None
) -> str | None:
    value = _section_value(sections, "work_type")
    if value:
        return value
    if title:
        lowered = title.lower()
        if "intern" in lowered:
            return "Internship"
        if "full time" in lowered or "full-time" in lowered:
            return "Full Time"
    match = re.search(r"\b(internship|full[- ]time|part[- ]time|contract)\b", text, re.IGNORECASE)
    if match:
        return {
            "internship": "Internship",
            "full time": "Full Time",
            "full-time": "Full Time",
            "part time": "Part Time",
            "part-time": "Part Time",
            "contract": "Contract",
        }[match.group(1).lower()]
    return None


def _parse_location(sections: dict[str, list[str]], lines: list[str]) -> str | None:
    value = _section_value(sections, "location")
    if value:
        return value
    for line in lines:
        match = re.match(
            r"(?:location|job\s+location|work\s+location)\s*[:\-]\s*(.+)",
            line,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()[:160]
    return None


# -- public API ---------------------------------------------------------


def parse_jd(
    text: str,
    *,
    title: str | None = None,
    company: str | None = None,
) -> ParsedJd:
    """Parse a job description into structured facts.

    ``company`` is accepted for API symmetry and future heuristics; it is not
    used to invent any field.
    """
    if not text or not str(text).strip():
        return ParsedJd(
            unknown_fields=[
                "skills",
                "rounds",
                "bond",
                "pay",
                "location",
                "work_type",
                "eligibility",
                "deadline",
                "degree",
                "min_cgpa",
                "graduation_year",
                "experience",
            ]
        )

    raw = str(text)
    if "<" in raw and ">" in raw:
        raw = html_to_text(raw)
    cleaned = _tidy(raw)
    lines = [line for line in cleaned.splitlines() if line]
    sections = _sections(lines)

    eligibility_text = _parse_eligibility(lines, sections)
    parsed = ParsedJd(
        skills=_parse_skills(lines, sections),
        rounds=_parse_rounds(lines, sections),
        bond=_parse_bond(cleaned),
        pay=_parse_pay(lines, sections),
        location=_parse_location(sections, lines),
        work_type=_parse_work_type(cleaned, sections, title),
        eligibility=eligibility_text,
        degree=_parse_degree(eligibility_text or ""),
        min_cgpa=_parse_cgpa(eligibility_text or ""),
        graduation_year=_parse_graduation_year(eligibility_text or ""),
        experience=_parse_experience(lines, sections),
        deadline=_parse_deadline(lines),
    )

    parsed.unknown_fields = [
        name
        for name in (
            "skills",
            "rounds",
            "bond",
            "pay",
            "location",
            "work_type",
            "eligibility",
            "deadline",
            "degree",
            "min_cgpa",
            "graduation_year",
            "experience",
        )
        if getattr(parsed, name) is None
    ]
    return parsed
