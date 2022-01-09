from argparse import ArgumentParser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence


@dataclass
class Options:
    logfile_path: Path
    settings_path: Path


def resolve_path(p: str) -> Path:  # pragma: no cover
    """Construct the path from p and resolve it to lock the effects of cwd"""
    return Path(p).resolve()


def get_options(
    default_settings_path: Path, args: Optional[Sequence[str]] = None
) -> Options:
    # We pass args manually in testing -> don't exit on error
    parser = ArgumentParser(exit_on_error=args is None)

    parser.add_argument(
        "logfile",
        help="Path to launcher_log.txt",
        type=resolve_path,
    )

    parser.add_argument(
        "-s",
        "--settings",
        help="Path to the .toml settings-file",
        type=resolve_path,
        default=default_settings_path,
    )

    # Parse the args
    # Parses from sys.argv if args is None
    parsed = parser.parse_args(args=args)

    assert isinstance(parsed.logfile, Path)
    assert isinstance(parsed.settings, Path)

    return Options(logfile_path=parsed.logfile, settings_path=parsed.settings)