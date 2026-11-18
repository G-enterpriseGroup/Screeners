"""Streamlit boundary for the mandatory, server-side Terminal identity policy."""

from functools import wraps
import re

import streamlit as st

from src.security_store import GitHubSecurityStore, SecurityStoreConfig, SecurityStoreError
from src.terminal_security import AuthService, SecurityConfigurationError, load_policy


def get_auth_service() -> AuthService:
    # Re-read configuration on every entry; never cache an enabled role or secret.
    values = st.secrets.to_dict()
    return AuthService(load_policy(values), GitHubSecurityStore(SecurityStoreConfig.from_runtime(values)))


def security_service_status() -> tuple[AuthService | None, str]:
    try:
        return get_auth_service(), ""
    except (SecurityConfigurationError, SecurityStoreError) as exc:
        return None, str(exc)
    except Exception:
        return None, "Security configuration is unavailable. Access remains locked."


def current_principal(*, touch: bool = False):
    try:
        return get_auth_service().authenticated(st.session_state, touch=touch)
    except Exception:
        return None


def clear_login_session() -> None:
    from src.terminal_security import AUTH_SESSION_KEY, PENDING_SESSION_KEY
    for key in (AUTH_SESSION_KEY, PENDING_SESSION_KEY, "etrade_access_unlocked",
                "_touchid_persist_then_unlock", "_terminal_enroll_after_mfa"):
        st.session_state.pop(key, None)


def clear_expired_session() -> None:
    # A revoked/expired session must lose feature caches as well as its token.
    # The sealed public WebAuthn record can be restored from this browser again.
    st.session_state.clear()


def require_finance_access(*, touch: bool = False):
    principal = current_principal(touch=touch)
    if principal and principal.can_access_finance and st.session_state.get("etrade_access_unlocked"):
        return principal
    clear_expired_session()
    st.rerun(scope="app")
    st.stop()
    raise PermissionError("Financial access requires a valid owner MFA session.")


def finance_fragment(func=None, **options):
    """Guard fragment-only reruns, which do not execute streamlit_app.py."""
    def decorate(render):
        @wraps(render)
        def protected(*args, **kwargs):
            require_finance_access()
            return render(*args, **kwargs)
        return st.fragment(protected, **options)
    return decorate(func) if func is not None else decorate


def finance_dialog(title, **options):
    """Dialogs also rerun independently of their parent financial fragment."""
    def decorate(render):
        @wraps(render)
        def protected(*args, **kwargs):
            require_finance_access()
            return render(*args, **kwargs)
        return st.dialog(title, **options)(protected)
    return decorate


def finance_callback(render):
    """Protect broker callbacks, which execute before their fragment's body."""
    @wraps(render)
    def protected(*args, **kwargs):
        require_finance_access(touch=False)
        return render(*args, **kwargs)
    return protected


@st.fragment(run_every="30s")
def render_access_watchdog() -> None:
    # Polling must never extend the idle deadline. User-initiated app reruns do.
    require_finance_access()


def render_pin_reset_request() -> bool:
    """Public recovery route; the financial app and its imports stay unloaded."""
    if "pin_reset" not in st.query_params and not st.session_state.get("_terminal_pin_reset_token"):
        return False
    from src.pin_recovery import RecoveryConfig, RecoveryService

    supplied = st.query_params.get("pin_reset")
    if supplied:
        st.session_state["_terminal_pin_reset_token"] = str(supplied)
        del st.query_params["pin_reset"]
    st.set_page_config(page_title="Reset Terminal PIN", layout="centered")
    with st.container(key="terminal_pin_reset"):
        st.html("<style>.st-key-terminal_pin_reset :is(h1,h2,p,label,span,a){color:#fb8b1e!important}</style>")
        st.title("Reset Terminal PIN")
        st.write("Choose a PIN with 8–32 digits. Your authenticator is still required to sign in.")
        with st.form("terminal_pin_reset_form", clear_on_submit=True):
            pin = st.text_input("New PIN", type="password", max_chars=32)
            confirm = st.text_input("Confirm new PIN", type="password", max_chars=32)
            submit = st.form_submit_button("Save new PIN")
        if submit:
            if pin != confirm:
                st.error("The PINs do not match.")
            elif not re.fullmatch(r"[0-9]{8,32}", pin):
                st.error("Use a PIN containing 8–32 digits.")
            else:
                try:
                    recovery = RecoveryService(get_auth_service(), RecoveryConfig.from_runtime(st.secrets.to_dict()))
                    changed = recovery.redeem(st.session_state.get("_terminal_pin_reset_token", ""), pin)
                    if changed:
                        clear_login_session()
                        st.session_state.pop("_terminal_pin_reset_token", None)
                        st.success("PIN changed. Sign in with your new PIN and authenticator.")
                    else:
                        st.error("This reset link is invalid or expired. Request another from the login page.")
                except ValueError:
                    st.error("Use a PIN containing 8–32 digits.")
                except Exception:
                    st.error("PIN recovery is unavailable. Your PIN has not been confirmed changed.")
        if st.button("Return to sign in", key="terminal_leave_pin_reset"):
            st.session_state.pop("_terminal_pin_reset_token", None)
            st.query_params.pop("pin_reset", None)
            st.rerun()
    return True


def render_recovery_link(user_id: str) -> None:
    from src.pin_recovery import RecoveryConfig, RecoveryService
    try:
        config = RecoveryConfig.from_runtime(st.secrets.to_dict())
        service = get_auth_service()
    except Exception:
        st.caption("PIN recovery: contact enterpriseghotra@gmail.com. Email reset is available after mail setup.")
        return
    if st.button("Forgot PIN? Email a reset link", key="terminal_request_pin_reset"):
        try:
            RecoveryService(service, config).request(user_id)
        except Exception:
            # Do not expose account existence, delivery details, or mail secrets.
            pass
        st.info("If this identity is eligible, a reset link will be sent to its registered email. Check your inbox and spam folder.")
