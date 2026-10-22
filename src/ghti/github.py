"""Output helpers: GitHub Actions job summary, annotations and the
"please authorize again" instructions."""

from __future__ import annotations

import os
import sys

from ghti.oauth import REDIRECT_URI


def in_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def write_summary(markdown: str) -> None:
    """Append to the job summary (shown on the workflow run page)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(markdown.rstrip() + "\n\n")


def warning(message: str) -> None:
    if in_actions():
        print(f"::warning::{message}")
    else:
        print(f"Warning: {message}", file=sys.stderr)


def mask_in_logs(value: str) -> None:
    """Ask GitHub to hide a value in the following log lines."""
    if in_actions() and value:
        print(f"::add-mask::{value}")


def _reauth_workflow_url() -> str | None:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return None
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    return f"{server}/{repo}/actions/workflows/reauth.yml"


def auth_instructions(link: str, title: str, reason: str = "") -> str:
    """Markdown steps to (re)authorize, for GitHub or for a local terminal."""
    workflow = _reauth_workflow_url()
    if workflow:
        last_step = (
            f"3. Open [Actions > Re-authorize]({workflow}), click **Run workflow**, paste the "
            "address into **redirect_url** and click the green **Run workflow** button."
        )
    else:
        last_step = '3. Run: `ghti auth-finish "<the address you copied>"`'
    lines = [f"## {title}", ""]
    if reason:
        lines += [reason, ""]
    lines += [
        'Google asks personal apps in "Testing" mode to be approved again every 7 days. It takes a minute:',
        "",
        f"1. Open this link and approve access: **[Authorize Google Health]({link})**",
        "   Google warns that the app has not been verified: it is your own app, so click *Continue*.",
        '2. At the end the browser shows an error such as "This site can\'t be reached". That is expected.',
        f"   Copy the **whole address** from the address bar. It starts with `{REDIRECT_URI}?state=`.",
        last_step,
        "",
        "Do steps 2 and 3 within a few minutes: the code in the address expires quickly.",
        "The days missed in the meantime are synced right after.",
    ]
    return "\n".join(lines) + "\n"
