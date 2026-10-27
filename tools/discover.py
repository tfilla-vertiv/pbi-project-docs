"""Find the Power BI project this package should document.

Search starts at the package folder and walks upward. The nearest folder that
contains one ``.pbip`` file wins. If a folder has no ``.pbip`` but has one
``.Report`` folder, that layout is used instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class ProjectError(Exception):
    """The Power BI project could not be located or read."""


@dataclass(frozen=True)
class PowerBIProject:
    """Paths for one PBIP project."""

    root: Path
    name: str
    pbip: Path | None
    report_dir: Path
    model_dir: Path | None
    model_note: str = ""


def discover_project(start: Path | None = None) -> PowerBIProject:
    """Return the nearest Power BI project at or above ``start``."""
    origin = (start or PACKAGE_ROOT).resolve()
    searched: list[Path] = []
    for directory in (origin, *origin.parents):
        searched.append(directory)
        try:
            pbips = sorted(
                (path for path in directory.glob("*.pbip") if path.is_file()),
                key=lambda path: path.name.casefold(),
            )
        except OSError:
            continue
        if len(pbips) > 1:
            listed = "\n".join(f"  {path.name}" for path in pbips)
            raise ProjectError(
                f"{directory} contains more than one Power BI project:\n{listed}\n"
                "Pass --model-dir and --report-dir to choose one."
            )
        if len(pbips) == 1:
            return _from_pbip(pbips[0])
        project = _from_folders(directory)
        if project is not None:
            return project
    looked = "\n".join(f"  {path}" for path in searched[:8])
    extra = "\n  ..." if len(searched) > 8 else ""
    raise ProjectError(
        "No Power BI project found. Unzip this package into the folder that contains "
        "the .pbip file (a .pbix file is not enough), then run it again.\n"
        f"Looked in:\n{looked}{extra}"
    )


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ProjectError(f"Could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProjectError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProjectError(f"{path} is not a JSON object.")
    return data


def _resolve_under(base: Path, raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    return (base / path).resolve()


def _report_definition(report_root: Path) -> Path:
    definition = report_root / "definition"
    pages = definition / "pages" / "pages.json"
    if pages.is_file():
        return definition
    raise ProjectError(
        f"Report definition not found at {pages}. Save the file as a Power BI "
        "project (.pbip) with a PBIR report, then run this again. A .pbix file "
        "cannot be documented directly."
    )


def _model_from_report(report_root: Path) -> tuple[Path | None, str]:
    pbir = report_root / "definition.pbir"
    if not pbir.is_file():
        matched = _matching_model(report_root)
        if matched is not None:
            return matched, ""
        raise ProjectError(f"Report metadata not found: {pbir}")
    ref = _read_json(pbir).get("datasetReference")
    if not isinstance(ref, dict):
        raise ProjectError(f"{pbir} has no datasetReference.")
    by_path = ref.get("byPath")
    if isinstance(by_path, dict) and isinstance(by_path.get("path"), str):
        model_root = _resolve_under(report_root, by_path["path"])
        definition = model_root / "definition"
        if not definition.is_dir():
            raise ProjectError(
                f"Semantic model definition not found at {definition}. "
                f"{pbir.name} points at {by_path['path']}."
            )
        return definition, ""
    if "byConnection" in ref:
        return None, (
            f"{report_root.name} uses a remote semantic model. This package needs "
            "the model's TMDL folder. Save the project so it includes the semantic "
            "model, or pass --model-dir pointing at that model's definition folder "
            "and --report-dir pointing at the report definition folder."
        )
    raise ProjectError(f"{pbir} does not reference a local semantic model.")


def _matching_model(report_root: Path) -> Path | None:
    stem = report_root.name.removesuffix(".Report")
    candidate = report_root.parent / f"{stem}.SemanticModel" / "definition"
    if candidate.is_dir():
        return candidate.resolve()
    return None


def _from_pbip(pbip: Path) -> PowerBIProject:
    data = _read_json(pbip)
    reports: list[str] = []
    for artifact in data.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        report = artifact.get("report")
        if isinstance(report, dict) and isinstance(report.get("path"), str):
            reports.append(report["path"])
    if not reports:
        raise ProjectError(f"{pbip} does not name a report.")
    if len(reports) > 1:
        listed = "\n".join(f"  {path}" for path in reports)
        raise ProjectError(
            f"{pbip.name} names more than one report:\n{listed}\n"
            "Pass --report-dir and --model-dir to choose one."
        )
    report_root = _resolve_under(pbip.parent, reports[0])
    if not report_root.is_dir():
        raise ProjectError(f"{pbip.name} points at a missing report folder: {report_root}")
    model_dir, note = _model_from_report(report_root)
    return PowerBIProject(
        root=pbip.parent.resolve(),
        name=pbip.stem,
        pbip=pbip.resolve(),
        report_dir=_report_definition(report_root),
        model_dir=model_dir,
        model_note=note,
    )


def _from_folders(directory: Path) -> PowerBIProject | None:
    try:
        children = list(directory.iterdir())
    except OSError:
        return None
    reports = sorted(
        (
            child for child in children
            if child.is_dir()
            and child.name.endswith(".Report")
            and (child / "definition" / "pages" / "pages.json").is_file()
        ),
        key=lambda path: path.name.casefold(),
    )
    if not reports:
        return None
    if len(reports) > 1:
        listed = "\n".join(f"  {path.name}" for path in reports)
        raise ProjectError(
            f"{directory} contains more than one report folder:\n{listed}\n"
            "Pass --model-dir and --report-dir to choose one."
        )
    report_root = reports[0]
    model_dir, note = _model_from_report(report_root)
    return PowerBIProject(
        root=directory.resolve(),
        name=report_root.name.removesuffix(".Report"),
        pbip=None,
        report_dir=_report_definition(report_root),
        model_dir=model_dir,
        model_note=note,
    )
