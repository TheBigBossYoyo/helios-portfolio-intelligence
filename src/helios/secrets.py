"""Credential storage that keeps secrets out of the repository and off disk in plaintext.

Helios accepts credentials from the dashboard, which means it needs somewhere to put them. The
OS keyring is that somewhere: on Windows it is Credential Manager, on macOS the Keychain, on
Linux whatever Secret Service is running. A `.env` file is the fallback, used only when no
keyring backend is available, because a plaintext file readable by every process running as you
is strictly worse than an encrypted store the OS gates.

Two properties this module holds to:

* **A stored secret is never returned.** :func:`credential_hint` gives back the last four
  characters and nothing else, which is enough for a person to recognise a key they pasted and
  useless to anyone who did not already have it.
* **A missing backend is reported, not hidden.** :func:`keyring_available` probes the backend
  once by round-tripping a throwaway value, so the dashboard can say "this went to a plaintext
  file" instead of implying an encrypted store that is not there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import keyring
from keyring.errors import KeyringError

#: Keyring service name. Every Helios credential lives under this service, distinguished by the
#: username field, so uninstalling means clearing one service rather than hunting five entries.
KEYRING_SERVICE: Final = "helios"

#: The credentials the dashboard can set, keyed by the ``Settings`` field they populate.
#:
#: The value is the keyring username. It matches the field name deliberately: a person inspecting
#: Credential Manager should be able to tell what each entry is for without consulting this file.
CREDENTIAL_KEYS: Final[dict[str, str]] = {
    "t212_api_key": "t212_api_key",
    "t212_api_secret": "t212_api_secret",
    "openfigi_api_key": "openfigi_api_key",
    "market_data_api_key": "market_data_api_key",
    "market_data_fallback_api_key": "market_data_fallback_api_key",
    "news_marketaux_api_key": "news_marketaux_api_key",
    "anthropic_api_key": "anthropic_api_key",
}

#: Characters of a stored secret revealed in a hint. Four is enough to recognise a key you
#: pasted; it is not enough to reconstruct one.
HINT_LENGTH: Final = 4

#: Shortest secret that gets a hint at all. Below this, four characters is most of the value.
MIN_HINTABLE_LENGTH: Final = 8


class CredentialStoreError(RuntimeError):
    """The keyring backend refused an operation."""


@dataclass(frozen=True)
class KeyringStatus:
    """What the keyring backend can actually do, as measured rather than assumed."""

    available: bool
    backend: str
    detail: str


def keyring_available() -> KeyringStatus:
    """Probe the keyring by round-tripping a throwaway value.

    ``keyring.get_keyring()`` reports a backend even when that backend is the null one that
    silently discards writes, so naming it is not evidence it works. Writing and reading back is.
    """

    backend = type(keyring.get_keyring()).__name__
    probe_user = "__helios_probe__"
    try:
        keyring.set_password(KEYRING_SERVICE, probe_user, "probe")
        echoed = keyring.get_password(KEYRING_SERVICE, probe_user)
        keyring.delete_password(KEYRING_SERVICE, probe_user)
    except KeyringError as exc:
        return KeyringStatus(available=False, backend=backend, detail=str(exc))
    except Exception as exc:
        return KeyringStatus(available=False, backend=backend, detail=str(exc))

    if echoed != "probe":
        return KeyringStatus(
            available=False,
            backend=backend,
            detail="backend accepted a write but did not return it; secrets would be discarded",
        )
    return KeyringStatus(available=True, backend=backend, detail="verified by round-trip")


def get_credential(field: str) -> str | None:
    """Read a credential, or ``None`` when it is absent or the backend is unreachable.

    An unreachable backend is not an error here: the caller falls back to environment values,
    which is the documented behaviour when no keyring exists.
    """

    username = CREDENTIAL_KEYS.get(field)
    if username is None:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, username)
    except KeyringError:
        return None
    except Exception:
        return None


def set_credential(field: str, value: str) -> None:
    """Store a credential, raising when the backend cannot hold it.

    Unlike :func:`get_credential` this does not swallow failures. A silent failure here would
    leave the operator believing a key was saved when it was not.
    """

    username = CREDENTIAL_KEYS.get(field)
    if username is None:
        raise CredentialStoreError(f"{field} is not a storable credential")
    try:
        keyring.set_password(KEYRING_SERVICE, username, value)
    except KeyringError as exc:
        raise CredentialStoreError(f"keyring refused to store {field}: {exc}") from exc
    except Exception as exc:
        raise CredentialStoreError(f"keyring refused to store {field}: {exc}") from exc


def delete_credential(field: str) -> bool:
    """Remove a credential. Returns whether anything was actually removed."""

    username = CREDENTIAL_KEYS.get(field)
    if username is None:
        return False
    try:
        keyring.delete_password(KEYRING_SERVICE, username)
    except KeyringError:
        return False
    except Exception:
        return False
    return True


def credential_hint(value: str | None) -> str | None:
    """The tail of a secret, for recognition only.

    Short values get no hint at all: revealing four characters of a six-character string gives
    away most of it.
    """

    if not value:
        return None
    if len(value) < MIN_HINTABLE_LENGTH:
        return "…"
    return f"…{value[-HINT_LENGTH:]}"
