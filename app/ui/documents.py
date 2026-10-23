"""Documents page: browse the research documents the agent writes."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

RESEARCH_DIR = Path("research")


def _documents() -> list[Path]:
    if not RESEARCH_DIR.is_dir():
        return []
    files = [p for p in RESEARCH_DIR.rglob("*.md") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def render() -> None:
    st.subheader("Research documents")
    st.caption(
        "One markdown file per detected posting, written by the research agent "
        "at `research/<company>/<date>-<role>.md`."
    )

    files = _documents()
    if not files:
        st.info(
            "No documents yet. They are created when a new or changed posting "
            "is detected. To try it now run `python -m app.main demo --send`."
        )
        return

    with st.sidebar:
        st.markdown("### Documents")
        company_filter = st.selectbox(
            "Company", ["All"] + sorted({p.parent.name for p in files}), key="d_company"
        )
        search = st.text_input("Search text", key="d_search", placeholder="Keyword")

    if company_filter != "All":
        files = [p for p in files if p.parent.name == company_filter]

    st.caption(f"{len(files)} document(s)")

    for path in files[:200]:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        title = content.splitlines()[0].lstrip("# ").strip() if content else path.stem

        if search and search.strip().lower() not in content.lower():
            continue

        with st.expander(title):
            st.caption(str(path))
            if search and search.strip():
                st.caption(f"Showing matches for '{search.strip()}'")
                for line in content.splitlines():
                    if search.strip().lower() in line.lower():
                        st.text(line[:300])
            else:
                st.markdown(content[:8000])
            st.download_button(
                "Download",
                data=content,
                file_name=path.name,
                key=f"dl_{path}",
            )