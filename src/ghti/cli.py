"""Command line: ghti sync | backfill | auth-url | auth-finish | export-csv | status.

Exit codes: 0 ok, 1 error, 2 configuration problem, 3 Google authorization needed.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

from ghti import __version__
from ghti.config import REQUIRED, is_configured, load_settings, raw_values, redact
from ghti.crypto import Keys
from ghti.errors import AuthRequired, ConfigError, GhtiError
from ghti.github import auth_instructions, in_actions, mask_in_logs, warning, write_summary
from ghti.oauth import REDIRECT_URI, TokenStore, build_auth_url, exchange_code, parse_redirect_url
from ghti.sync import SyncReport, export_csv, run_sync

MAX_DAYS = 3650


def _http() -> httpx.Client:
    return httpx.Client(timeout=30.0, headers={"User-Agent": f"google-health-to-intervals/{__version__}"})


def _keys(settings) -> Keys:
    return Keys(settings.google_client_secret, settings.intervals_api_key)


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a date in the form YYYY-MM-DD") from None


def _auth_text(link: str) -> str:
    """Plain-text instructions for a terminal."""
    return (
        "Open this link in your browser and approve access:\n\n"
        f"{link}\n\n"
        "Google warns that the app has not been verified: it is your own app, click Continue.\n"
        'At the end the browser shows an error ("This site can\'t be reached"). That is expected.\n'
        f"Copy the whole address from the address bar (it starts with {REDIRECT_URI}?state=)\n"
        'and, within a few minutes, run:  ghti auth-finish "<the address>"\n'
    )


def _show_auth(settings, title: str, reason: str = "") -> None:
    link = build_auth_url(settings, _keys(settings), encode_client_id=in_actions())
    if in_actions():
        markdown = auth_instructions(link, title, reason)
        write_summary(markdown)
        print(markdown)
    else:
        print((reason + "\n\n" if reason else "") + _auth_text(link))


def _report(report: SyncReport, show_values: bool) -> int:
    print(report.text(show_values))
    write_summary(report.markdown(show_values))
    for name, error in sorted(report.failed.items()):
        warning(f"{name}: {error}")
    if report.skipped:
        warning("Not authorized: " + ", ".join(sorted(report.skipped)) + ". Tick every box at the next authorization.")
    if report.token_updated:
        print("The stored Google token was updated.")
    return 0


# --- commands ------------------------------------------------------------------


def _unconfigured(args) -> bool:
    """The template repository itself has no secrets: its scheduled runs do nothing."""
    if not args.skip_if_unconfigured or is_configured():
        return False
    message = "Not configured yet: add the repository secrets described in the README. Nothing to do."
    print(message)
    write_summary("## Google Health to intervals.icu\n\n" + message)
    return True


def cmd_sync(args) -> int:
    if _unconfigured(args):
        return 0
    if not 1 <= args.days <= MAX_DAYS:
        raise ConfigError(f"--days must be between 1 and {MAX_DAYS}.")
    settings = load_settings()
    end = settings.today()
    start = end - timedelta(days=args.days - 1)
    with _http() as http:
        report = run_sync(settings, start, end, http, dry_run=args.dry_run)
    return _report(report, args.show_values)


def cmd_backfill(args) -> int:
    settings = load_settings()
    end = args.to or settings.today()
    if args.start > end:
        raise ConfigError(f"--from ({args.start}) is after --to ({end}).")
    if (end - args.start).days >= MAX_DAYS:
        raise ConfigError(f"The range is longer than {MAX_DAYS} days.")
    with _http() as http:
        report = run_sync(settings, args.start, end, http, dry_run=args.dry_run)
    return _report(report, args.show_values)


def cmd_auth_url(args) -> int:
    _show_auth(load_settings(), "Authorize Google Health")
    return 0


def cmd_auth_finish(args) -> int:
    settings = load_settings()
    address = args.url or os.environ.get("GHTI_REDIRECT_URL", "")
    if not address.strip():
        raise ConfigError('Paste the address from the browser: ghti auth-finish "http://127.0.0.1:8765/?state=..."')
    with contextlib.suppress(GhtiError):  # exchange_code below explains a bad address
        mask_in_logs(parse_redirect_url(address)[0])  # hide the one-time code in GitHub logs
    keys = _keys(settings)
    with _http() as http:
        token, missing = exchange_code(settings, keys, address, http)
    TokenStore(settings.token_file, keys).save(token)
    expires = token.expires_at.strftime("%Y-%m-%d %H:%M UTC")
    message = f"Google authorization saved. It expires on {expires}."
    print(message)
    if not in_actions():
        print(f"Token file: {settings.token_file}")
    summary = f"## Google authorization saved\n\n{message}\n"
    if missing:
        names = ", ".join(scope.rsplit(".", 2)[-2] for scope in missing)
        warning(f"Some permissions were not granted ({names}): that data will not be copied.")
        summary += f"\nNot granted: {names}. Authorize again and tick every box to copy that data.\n"
    write_summary(summary)
    return 0


def cmd_export_csv(args) -> int:
    settings = load_settings()
    end = args.to or settings.today()
    start = args.start or end - timedelta(days=args.days - 1)
    if start > end:
        raise ConfigError(f"--from ({start}) is after --to ({end}).")
    with _http() as http:
        text, report = export_csv(settings, start, end, http)
    if args.output == "-":
        sys.stdout.write(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8", newline="")
        print(f"Wrote {len(report.days)} day(s) to {args.output}")
    for name, error in sorted(report.failed.items()):
        warning(f"{name}: {error}")
    return 0


def cmd_status(args) -> int:
    if _unconfigured(args):
        return 0
    settings = load_settings()
    token = TokenStore(settings.token_file, _keys(settings)).load()
    remaining = token.remaining()
    expires = token.expires_at.strftime("%Y-%m-%d %H:%M UTC")
    if remaining <= timedelta(0):
        raise AuthRequired(f"The Google authorization expired on {expires}.")
    hours = int(remaining.total_seconds() // 3600)
    print(f"Google authorization from {token.obtained_at:%Y-%m-%d}, expires on {expires} (in {hours} h).")
    if remaining <= timedelta(hours=args.remind_hours):
        when = "today" if hours < 12 else "tomorrow"
        _show_auth(
            settings,
            f"Reminder: the Google authorization expires {when}",
            f"It expires on {expires} (in about {hours} hours). Renew it now and the sync will not stop.",
        )
        return 1 if args.fail_on_reminder else 0
    write_summary(f"## Google authorization OK\n\nIt expires on {expires} (in {hours} hours).")
    return 0


# --- parser and entry point ------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghti", description="Copy Google Health (Fitbit, Pixel Watch) wellness data to intervals.icu."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sync", help="copy the last N days (default 3); safe to repeat")
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--dry-run", action="store_true", help="read from Google but do not write to intervals.icu")
    p.add_argument("--show-values", action="store_true", help="print the values (avoid in public logs)")
    p.add_argument("--skip-if-unconfigured", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("backfill", help="copy a past period")
    p.add_argument("--from", dest="start", type=_day, required=True, metavar="YYYY-MM-DD")
    p.add_argument("--to", type=_day, metavar="YYYY-MM-DD", help="default: today")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--show-values", action="store_true")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("auth-url", help="print the Google consent link")
    p.set_defaults(func=cmd_auth_url)

    p = sub.add_parser("auth-finish", help="save the authorization from the browser address")
    p.add_argument("url", nargs="?", help="the full address from the browser (or env GHTI_REDIRECT_URL)")
    p.set_defaults(func=cmd_auth_finish)

    p = sub.add_parser("export-csv", help="write the data as CSV instead of sending it")
    p.add_argument("--from", dest="start", type=_day, metavar="YYYY-MM-DD")
    p.add_argument("--to", type=_day, metavar="YYYY-MM-DD", help="default: today")
    p.add_argument("--days", type=int, default=30, help="used when --from is not given (default 30)")
    p.add_argument("--output", "-o", default="-", help="file name, or - for the screen (default)")
    p.set_defaults(func=cmd_export_csv)

    p = sub.add_parser("status", help="show when the Google authorization expires")
    p.add_argument("--remind-hours", type=float, default=36, help="remind when fewer hours are left (default 36)")
    p.add_argument("--fail-on-reminder", action="store_true", help="exit with code 1 when reminding")
    p.add_argument("--skip-if-unconfigured", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_status)
    return parser


def _secrets() -> tuple[str, ...]:
    values = raw_values()
    return tuple(values.get(key, "") for key in REQUIRED if key != "GOOGLE_CLIENT_ID")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except AuthRequired as exc:
        try:
            _show_auth(load_settings(), "Google authorization needed", str(exc))
        except ConfigError:
            print(str(exc), file=sys.stderr)
        return exc.exit_code
    except GhtiError as exc:
        message = redact(str(exc), _secrets())
        print(f"Error: {message}", file=sys.stderr)
        write_summary(f"## Error\n\n{message}")
        return exc.exit_code
    except httpx.HTTPError as exc:
        message = redact(f"Network problem: {exc.__class__.__name__}: {exc}", _secrets())
        print(f"Error: {message}", file=sys.stderr)
        write_summary(f"## Error\n\n{message}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
