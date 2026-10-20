"""Path checks shared by the loader (input) and the cleaner's writer (output).

Every check here works on the raw string and runs before any filesystem call:
on Windows, even a stat on a UNC path opens an SMB connection to that host,
which can leak the user's NTLM credentials.
"""

import os

from data_quality_mcp import config
from data_quality_mcp.profiler_core.exceptions import InvalidInputError


def check_local_path(
    raw: str,
    *,
    allow_network_paths: bool = config.ALLOW_NETWORK_PATHS,
    windows: bool = os.name == "nt",
) -> None:
    """Reject UNC and device paths on Windows.

    That is \\\\server\\share, \\\\?\\, \\\\.\\ and the NT prefix \\??\\.

    Forward-slash forms (//server/share) are normalised first, so they are
    caught too. On POSIX there is no SMB redirection, and a leading
    backslash is an ordinary file-name character, so nothing is checked.
    """
    if allow_network_paths or not windows:
        return
    normalised = raw.replace("/", "\\")
    # \\server\share, \\?\..., \\.\... and the NT object prefix \??\ (which
    # Windows passes through, e.g. \??\UNC\server\share)
    if normalised.startswith("\\\\") or normalised.startswith("\\??\\"):
        raise InvalidInputError(
            f"'{raw}' is a network or device path, which this server doesn't open "
            "(on Windows that would connect to another host and can leak your "
            "credentials). Copy the file to a local folder and pass that path."
        )
