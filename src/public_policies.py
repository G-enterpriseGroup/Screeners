"""Public policy routes that never load terminal accounts, secrets or broker code."""

from __future__ import annotations

from pathlib import Path


_POLICY_ROOT = Path(__file__).resolve().parents[1] / "docs" / "policies"
POLICIES = {
    "privacy": ("Privacy policy", "privacy.md"),
    "security": ("Security and access policy", "security.md"),
    "retention": ("Data retention and deletion", "retention.md"),
    "vulnerability-management": ("Vulnerability management", "vulnerability-management.md"),
}
CONTACT_EMAIL = "enterpriseghotra@gmail.com"


def policy_document(slug: str) -> tuple[str, str] | None:
    """Resolve an allowlisted document; a URL value can never become a path."""
    entry = POLICIES.get(slug)
    if entry is None:
        return None
    title, filename = entry
    return title, (_POLICY_ROOT / filename).read_text(encoding="utf-8")


def render_policy_links() -> None:
    import streamlit as st

    st.markdown(
        "[Privacy](/?policy=privacy) · [Security](/?policy=security) · "
        "[Data retention](/?policy=retention) · "
        "[Vulnerability management](/?policy=vulnerability-management) · "
        "[Contact](mailto:" + CONTACT_EMAIL + ")"
    )


def render_public_policy_request() -> bool:
    """Return True for policy requests so the entrypoint stops before core loading."""
    import streamlit as st

    if "policy" not in st.query_params:
        return False
    slug = str(st.query_params.get("policy", "") or "").strip().lower()
    document = policy_document(slug)
    st.set_page_config(page_title="Policies | Raj's Terminal", page_icon="🔒", layout="centered")
    with st.container(key="terminal_public_policy"):
        st.html("""<style>
        .st-key-terminal_public_policy :is(h1,h2,h3,h4,p,li,span,a) {color:#fb8b1e !important;}
        .st-key-terminal_public_policy a {text-decoration:underline;}
        </style>""")
        if document is None:
            st.title("Policy not found")
            st.write("Choose one of the public policy documents below.")
        else:
            title, body = document
            st.title(title)
            st.caption("Raj's Terminal · Effective November 17, 2026 · Version 1.0")
            st.markdown(body, unsafe_allow_html=False)
        render_policy_links()
        st.markdown("[Return to sign in](/)")
    return True
