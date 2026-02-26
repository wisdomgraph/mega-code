"""Secret masking filter.

Ported from megaupskill/src/classifier/secret-masker.ts.
Applies regex-based masking rules to replace sensitive values with '****'.
"""

import re

from mega_code.client.filters.base import TurnFilter

# Pattern tuples: (compiled_regex, replacement_template)
# Order matters — more specific patterns should come first.
DEFAULT_PATTERNS: list[tuple[re.Pattern, str]] = [
    # 1. Environment variable assignments with sensitive names
    (
        re.compile(
            r"((?:API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH|PRIVATE_KEY|"
            r"AWS_SECRET|AWS_ACCESS_KEY|GITHUB_TOKEN|NPM_TOKEN|SLACK_TOKEN|"
            r"DATABASE_URL|DB_PASSWORD)\s*=\s*)(\S+)",
            re.IGNORECASE,
        ),
        r"\1****",
    ),
    # 2. Bearer tokens
    (
        re.compile(r"(Bearer\s+)\S+", re.IGNORECASE),
        r"\1****",
    ),
    # 3. Authorization headers
    (
        re.compile(r"(Authorization:\s*(?:Bearer\s+)?)\S+", re.IGNORECASE),
        r"\1****",
    ),
    # 4. URLs with embedded credentials
    (
        re.compile(r"(https?://)([^:@\s]+):([^@\s]+)@", re.IGNORECASE),
        r"\1\2:****@",
    ),
    # 5. AWS access key patterns (AKIA, ABIA, ACCA, ASIA + 16 alphanums)
    (
        re.compile(r"(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}"),
        "****",
    ),
    # 6. Long hex/base64 strings that look like tokens (40+ chars)
    #    Uses fixed-length lookbehind for the preceding delimiter.
    #    Known tradeoff: may false-positive on legitimate base64 content,
    #    git SHA-1 hashes (40 hex chars), or checksums — favors security.
    (
        re.compile(r"(?<=[\s=:])[a-zA-Z0-9+/]{40,}(?=[\s'\"\n]|$)"),
        "****",
    ),
    # 7. CLI flags: --token, --password, --secret, --key, --api-key, --auth
    (
        re.compile(
            r"(--(?:token|password|secret|key|api-key|auth)\s*[=\s])\S+",
            re.IGNORECASE,
        ),
        r"\1****",
    ),
    # 8. PEM private key blocks (multi-line)
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
            re.MULTILINE,
        ),
        "****",
    ),
]


class SecretMasker(TurnFilter):
    """Mask secrets in text using regex patterns.

    Applies DEFAULT_PATTERNS plus any user-supplied extra_patterns.

    Args:
        extra_patterns: Additional (regex, replacement) tuples to apply
            after the defaults.
    """

    def __init__(
        self,
        extra_patterns: list[tuple[re.Pattern, str]] | None = None,
    ):
        self._patterns = DEFAULT_PATTERNS + (extra_patterns or [])

    def filter_text(self, text: str) -> str:
        """Apply all masking patterns sequentially.

        Args:
            text: Input text possibly containing secrets.

        Returns:
            Text with secrets replaced by '****'.
        """
        result = text
        for pattern, replacement in self._patterns:
            result = pattern.sub(replacement, result)
        return result
