"""Local deployment diagnostics and single-worker web service."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from . import __version__

def load_env(path: Path) -> None:
    """Read plain KEY=VALUE settings without executing arbitrary shell content."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key.isidentifier() or not key.isascii():
            raise ValueError("Invalid environment variable name in .env")
        value = value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1]:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def diagnose(config) -> int:
    errors = []
    try:
        config.validate()
    except ValueError as exc:
        errors.append(str(exc))
    if not config.admin_token:
        print("WARNING: ETA_ADMIN_TOKEN is unset; manual admin sync is disabled.")
    if config.mode == "files":
        for name in ("historical_jobs", "live_jobs", "plans"):
            path = Path(config.csv_dir) / f"{name}.csv"
            if not path.is_file():
                errors.append(f"Missing required CSV: {path}")
    if config.mode == "mssql":
        for name in ("historical_jobs", "live_jobs", "plans"):
            path = Path(config.query_dir) / f"{name}.sql"
            if not path.is_file():
                errors.append(f"Missing SQL query: {path}")
            elif "REPLACE_ME" in path.read_text(encoding="utf-8"):
                errors.append(f"Edit the SQL template before using it: {path}")
        for name in ("MSSQL_HOST", "MSSQL_USER", "MSSQL_DATABASE", "MSSQL_PASSWORD"):
            if not os.getenv(name) or os.getenv(name) in ("CHANGE_ME", "your_database",
                                                          "your-internal-sqlserver", "eta_readonly"):
                errors.append(f"Configure {name}")
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    print(f"Configuration OK (SOURCE_MODE={config.mode}); no database was changed.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="vedrfolnir", description="Vedrfolnir local deployment")
    parser.add_argument("--version", action="version", version=f"vedrfolnir {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("serve", "doctor"):
        sub = commands.add_parser(command)
        sub.add_argument("--env-file", default=".env", help="KEY=VALUE configuration file")
        if command == "serve":
            sub.add_argument("--host", default="127.0.0.1")
            sub.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    try:
        load_env(Path(args.env_file))
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    # Delay configuration import until the selected .env has been loaded.
    from .config import settings
    if args.command == "doctor":
        return diagnose(settings)
    settings.validate()
    import uvicorn
    uvicorn.run("vedrfolnir.api:app", host=args.host, port=args.port, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
