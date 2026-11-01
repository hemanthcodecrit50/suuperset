"""Builds tiny real PDFs for the resume import tests."""

from __future__ import annotations

RESUME_LINES = [
    "Asha Rao",
    "B.Tech in Computer Science, RV College of Engineering, 2023 - 2027",
    "CGPA: 8.4",
    "Skills: Python, Java, C, Data Structures",
    "Tools: PostgreSQL, Docker, React",
    "Projects",
    "Order Service - REST API for orders built with Python and PostgreSQL",
    "github.com/asha/order-service",
    "Campus Navigator - React app with live maps for new students",
    "Interests: Backend development, Machine learning",
]


PAGE_OBJECT = (
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
    "/Resources << /Font << /F1 5 0 R >> >> >>"
)


def make_pdf(lines: list[str]) -> bytes:
    text = " ".join(f"({_escape(line)}) '" for line in lines)
    content = f"BT /F1 11 Tf 50 760 Td 16 TL {text} ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        PAGE_OBJECT,
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body = "%PDF-1.4\n"
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n{obj}\nendobj\n"
    xref_at = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    body += "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    body += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    return body.encode("latin-1")


def _escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
