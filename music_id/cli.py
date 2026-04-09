from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from music_id.matcher import format_match_output
from music_id.service import (
    LibraryBuildError,
    QueryError,
    build_library,
    query_library,
    summarize_build_metadata,
)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="music-id",
        description="Offline music identification tool based on spectral peak fingerprints.",
    )

    subparsers = parser.add_subparsers(dest="command")

    build_parser = subparsers.add_parser(
        "build",
        help="Build or rebuild fingerprint index for a music library.",
    )
    build_parser.add_argument(
        "-d",
        "--directory",
        required=True,
        help="Path to the music library directory.",
    )
    build_parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Clear existing index and rebuild from scratch.",
    )
    build_parser.add_argument(
        "--thread",
        type=int,
        default=None,
        help="Build worker threads, e.g. --thread 2",
    )

    query_parser = subparsers.add_parser(
        "query",
        help="Query a recording against the local fingerprint index.",
    )
    query_parser.add_argument(
        "-d",
        "--directory",
        required=True,
        help="Path to the music library directory.",
    )
    query_parser.add_argument(
        "query_file",
        help="Path to the query audio file.",
    )
    query_parser.add_argument(
        "--no-auto-build",
        action="store_true",
        help="Do not auto-build index if it does not exist.",
    )
    query_parser.add_argument(
        "--thread",
        type=int,
        default=None,
        help="Build worker threads for auto-build, e.g. --thread 2",
    )

    ui_parser = subparsers.add_parser(
        "ui",
        help="Launch the local web UI.",
    )
    ui_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Web UI host, e.g. --host 127.0.0.1",
    )
    ui_parser.add_argument(
        "--port",
        type=int,
        default=7860,
        help="Web UI port, e.g. --port 7860",
    )
    ui_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the browser automatically when launching UI.",
    )

    parser.add_argument(
        "-d",
        "--directory",
        dest="default_directory",
        help="Path to the music library directory (default command mode).",
    )
    parser.add_argument(
        "default_query_file",
        nargs="?",
        help="Query audio file path (default command mode).",
    )
    parser.add_argument(
        "--rebuild",
        dest="default_rebuild",
        action="store_true",
        help="Rebuild index before query in default command mode.",
    )
    parser.add_argument(
        "--no-auto-build",
        dest="default_no_auto_build",
        action="store_true",
        help="Do not auto-build index if it does not exist in default command mode.",
    )
    parser.add_argument(
        "--thread",
        dest="default_thread",
        type=int,
        default=None,
        help="Build worker threads in default command mode, e.g. --thread 2",
    )

    return parser


def run_cli(argv: Optional[list[str]] = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "build":
            library_dir = Path(args.directory)
            metadata = build_library(
                library_dir,
                rebuild=args.rebuild,
                thread_count=args.thread,
            )
            print(summarize_build_metadata(metadata, library_dir))
            return 0

        if args.command == "query":
            library_dir = Path(args.directory)
            result = query_library(
                library_dir=library_dir,
                query_file=Path(args.query_file),
                auto_build=not args.no_auto_build,
                thread_count=args.thread,
            )
            print(format_match_output(result), end="")
            return 0

        if args.command == "ui":
            from music_id.ui import launch_ui

            launch_ui(
                server_name=args.host,
                server_port=args.port,
                inbrowser=not args.no_browser,
            )
            return 0

        if args.default_directory and args.default_query_file:
            library_dir = Path(args.default_directory)

            if args.default_rebuild:
                metadata = build_library(
                    library_dir,
                    rebuild=True,
                    thread_count=args.default_thread,
                )
                print(summarize_build_metadata(metadata, library_dir))

            result = query_library(
                library_dir=library_dir,
                query_file=Path(args.default_query_file),
                auto_build=not args.default_no_auto_build,
                thread_count=args.default_thread,
            )
            print(format_match_output(result), end="")
            return 0

        parser.print_help()
        return 1

    except (LibraryBuildError, QueryError, FileNotFoundError, NotADirectoryError, RuntimeError) as exc:
        print(f"Error: {exc}")
        return 2