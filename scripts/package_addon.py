#!/usr/bin/env python3
"""Validate and package a Kodi add-on as a versioned ZIP archive."""

import argparse
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree


VERSION_PATTERN = re.compile(r"^\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?$")
EXCLUDED_NAMES = {
    ".git",
    ".github",
    ".pytest_cache",
    "__pycache__",
    "build",
    "dist",
    "scripts",
}
EXCLUDED_FILES = {".DS_Store", ".gitignore"}


def parse_arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist"),
        help="Directory in which to write the ZIP archive (default: dist)",
    )
    parser.add_argument(
        "--expected-version",
        help="Require addon.xml to contain this version",
    )
    return parser.parse_args()


def read_metadata(root):
    metadata_path = root / "addon.xml"
    if not metadata_path.is_file():
        raise ValueError("addon.xml was not found at the repository root")

    try:
        metadata = ElementTree.parse(metadata_path).getroot()
    except ElementTree.ParseError as error:
        raise ValueError("addon.xml is not valid XML: {0}".format(error)) from error

    addon_id = metadata.get("id")
    version = metadata.get("version")
    if not addon_id:
        raise ValueError("addon.xml is missing the add-on id")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", addon_id):
        raise ValueError("addon.xml contains an invalid add-on id: {0}".format(addon_id))
    if not version or not VERSION_PATTERN.fullmatch(version):
        raise ValueError("addon.xml contains an invalid version: {0}".format(version))

    return addon_id, version


def files_to_package(root):
    for path in sorted(root.rglob("*")):
        relative_path = path.relative_to(root)
        if path.is_dir():
            continue
        if any(part in EXCLUDED_NAMES for part in relative_path.parts):
            continue
        if path.name in EXCLUDED_FILES or path.name.endswith((".pyc", ".pyo")):
            continue
        yield path, relative_path


def create_archive(root, output_directory, addon_id, version):
    output_directory.mkdir(parents=True, exist_ok=True)
    archive_path = output_directory / "{0}-{1}.zip".format(addon_id, version)
    if archive_path.exists():
        archive_path.unlink()

    with zipfile.ZipFile(
        archive_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path, relative_path in files_to_package(root):
            archive_name = Path(addon_id) / relative_path
            archive.write(path, archive_name.as_posix())

    return archive_path


def main():
    arguments = parse_arguments()
    root = Path(__file__).resolve().parents[1]

    try:
        addon_id, version = read_metadata(root)
        if arguments.expected_version and version != arguments.expected_version:
            raise ValueError(
                "version mismatch: addon.xml is {0}, expected {1}".format(
                    version, arguments.expected_version
                )
            )

        archive_path = create_archive(root, arguments.output.resolve(), addon_id, version)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print("Packaging failed: {0}".format(error), file=sys.stderr)
        return 1

    print("Created {0}".format(archive_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())