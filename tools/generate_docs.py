"""Generate Markdown documentation for the nearest Power BI project.

From the package folder, with the project discovered automatically::

    python tools/generate_docs.py

Explicit paths::

    python tools/generate_docs.py --model-dir "Report.SemanticModel/definition" --report-dir "Report.Report/definition" --output-dir docs
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from discover import PACKAGE_ROOT, ProjectError, discover_project
from tmdl_docs.loader import load_model
from tmdl_docs.render import render_documentation, write_documentation
from tmdl_docs.report import ReportSyntaxError, load_report
from tmdl_docs.tmdl import TmdlSyntaxError


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _href_from(output_dir: Path, target: Path) -> str:
    return Path(os.path.relpath(target.resolve(), output_dir.resolve())).as_posix()


def _shell_arg(text: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:\\-]+", text):
        return text
    return '"' + text.replace('"', '\\"') + '"'


def _ps_arg(text: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:\\-]+", text):
        return text
    return "'" + text.replace("'", "''") + "'"


def _resolve(path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate Markdown documentation from a Power BI project (PBIP).",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        help="Semantic-model definition folder. Discovered from the nearest .pbip when omitted.",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        help="PBIR report definition folder. Discovered from the nearest .pbip when omitted.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Documentation output folder. Defaults to docs/ beside the .pbip.",
    )
    return parser


def _regenerate_command(base: Path, model_dir: Path, report_dir: Path, output_dir: Path, using_defaults: bool) -> str:
    if using_defaults:
        launcher = PACKAGE_ROOT / "run.ps1"
        relative = Path(os.path.relpath(launcher.resolve(), base.resolve()))
        display = ".\\" + str(relative)
        return f"powershell -NoProfile -ExecutionPolicy Bypass -File {_ps_arg(display)}"
    script = Path(os.path.relpath(Path(__file__).resolve(), base.resolve())).as_posix()
    return (
        f"python {_shell_arg(script)} "
        f"--model-dir {_shell_arg(_display_path(model_dir, base))} "
        f"--report-dir {_shell_arg(_display_path(report_dir, base))} "
        f"--output-dir {_shell_arg(_display_path(output_dir, base))}"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    using_defaults = (
        args.model_dir is None and args.report_dir is None and args.output_dir is None
    )
    project = None
    if using_defaults or args.model_dir is None or args.report_dir is None or args.output_dir is None:
        try:
            project = discover_project()
        except ProjectError as exc:
            if args.model_dir is None or args.report_dir is None:
                print(f"error: {exc}", file=sys.stderr)
                return 1

    if args.model_dir is not None:
        model_dir = _resolve(args.model_dir)
    elif project is not None and project.model_dir is not None:
        model_dir = project.model_dir
    else:
        note = project.model_note if project is not None else "Semantic model folder was not found."
        print(f"error: {note}", file=sys.stderr)
        return 1

    if args.report_dir is not None:
        report_dir = _resolve(args.report_dir)
    elif project is not None:
        report_dir = project.report_dir
    else:
        print("error: report folder was not found.", file=sys.stderr)
        return 1

    base = project.root if project is not None else Path.cwd()
    if args.output_dir is not None:
        output_dir = _resolve(args.output_dir)
    elif project is not None:
        output_dir = project.root / "docs"
    else:
        output_dir = (Path.cwd() / "docs").resolve()

    try:
        model = load_model(model_dir)
        report = load_report(report_dir)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (TmdlSyntaxError, ReportSyntaxError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if project is not None:
        print(f"Documenting {project.name}")
        print(f"  {project.root}")
    model_display = _display_path(model_dir, base)
    report_display = _display_path(report_dir, base)
    files = render_documentation(
        model,
        model_dir_display=model_display,
        model_href=_href_from(output_dir, model_dir),
        regenerate_command=_regenerate_command(base, model_dir, report_dir, output_dir, using_defaults),
        using_defaults=using_defaults,
        report=report,
        report_dir_display=report_display,
        report_href=_href_from(output_dir, report_dir),
    )
    result = write_documentation(output_dir, files)
    print(f"Wrote {len(result.written)} Markdown files to {output_dir}")
    if result.removed:
        print(f"Removed {len(result.removed)} stale pages:")
        for rel in result.removed:
            print(f"  {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
