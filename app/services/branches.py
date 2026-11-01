"""BMSCE B.E. branches, the job areas each one prepares for, and reading a branch from text."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.jd_parser import find_degree


@dataclass(frozen=True)
class Branch:
    name: str
    areas: tuple[str, ...]
    # Lower-case ways a resume writes the branch; short ones must be whole words
    spellings: tuple[str, ...]


# Most specific first, so "CSE (Data Science)" is not read as plain CSE
BRANCHES = (
    Branch(
        "Computer Science and Engineering (Data Science)",
        ("software", "data"),
        ("computer science and engineering (data science)", "cse (data science)",
         "cse (ds)", "cse-ds", "computer science (data science)"),
    ),
    Branch(
        "Computer Science and Engineering (IoT and Cyber Security)",
        ("software", "hardware"),
        ("iot and cyber security", "internet of things", "cse (iot", "cse-iot"),
    ),
    Branch(
        "Artificial Intelligence and Machine Learning",
        ("data", "software"),
        ("artificial intelligence and machine learning", "ai&ml", "ai & ml", "ai and ml", "aiml"),
    ),
    Branch(
        "Artificial Intelligence and Data Science",
        ("data", "software"),
        ("artificial intelligence and data science", "ai&ds", "ai & ds", "ai and ds"),
    ),
    Branch(
        "Computer Science and Engineering",
        ("software", "data"),
        ("computer science", "cse"),
    ),
    Branch(
        "Information Science and Engineering",
        ("software", "data"),
        ("information science", "ise"),
    ),
    Branch(
        "Electronics and Telecommunication Engineering",
        ("hardware",),
        ("telecommunication", "electronics and telecom", "ete"),
    ),
    Branch(
        "Electronics and Instrumentation Engineering",
        ("hardware", "electrical"),
        ("electronics and instrumentation", "eie"),
    ),
    Branch(
        "Medical Electronics Engineering",
        ("hardware", "biotech"),
        ("medical electronics",),
    ),
    Branch(
        "Electronics and Communication Engineering",
        ("hardware",),
        ("electronics and communication", "ece"),
    ),
    Branch(
        "Electrical and Electronics Engineering",
        ("electrical", "hardware"),
        ("electrical and electronics", "eee"),
    ),
    Branch(
        "Industrial Engineering and Management",
        ("industrial", "business"),
        ("industrial engineering", "iem"),
    ),
    Branch("Aerospace Engineering", ("aerospace", "mechanical"), ("aerospace", "aeronautical")),
    Branch("Mechanical Engineering", ("mechanical",), ("mechanical",)),
    Branch("Civil Engineering", ("civil",), ("civil",)),
    Branch("Chemical Engineering", ("chemical",), ("chemical",)),
    Branch("Biotechnology", ("biotech",), ("biotechnology", "biotech")),
)
BRANCH_NAMES = tuple(branch.name for branch in BRANCHES)
SHORT_SPELLING_LENGTH = 4


def branch_areas(name: str | None) -> set[str]:
    """Job areas a branch prepares for; empty for an unknown or missing branch."""
    for branch in BRANCHES:
        if branch.name == name:
            return set(branch.areas)
    return set()


def find_branch(text: str) -> str | None:
    """The branch named on a resume's degree line, e.g. "B.E. in Mechanical Engineering"."""
    degree_lines = [line.lower() for line in text.splitlines() if find_degree(line)]
    for branch in BRANCHES:
        if any(_mentions(line, spelling) for line in degree_lines for spelling in branch.spellings):
            return branch.name
    return None


def _mentions(text: str, spelling: str) -> bool:
    if len(spelling) > SHORT_SPELLING_LENGTH:
        return spelling in text
    return re.search(rf"(?<![a-z]){re.escape(spelling)}(?![a-z])", text) is not None
