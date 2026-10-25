# SPDX-License-Identifier: Apache-2.0
"""Build a deterministic manual-install addon ZIP from an explicit public file list.

Build-time Python only; the installed addon uses FreeCAD's embedded Python.
No installed files, preferences, Git metadata or machine paths are collected.
"""
import argparse
import ast
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import stat
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ADDON = "PhoenixFreeCADTools"
ADDON_FILES = (
    "Init.py", "InitGui.py", "package.xml",
    "pft_bridge.py", "pft_business.py",
    "pft_part_approval_views.py", "pft_pad_length.py", "pft_pad_contract.py",
    "pft_operations.py", "pft_runtime.py", "pft_document_state.py", "pft_units.py",
    "pft_workbench_controller.py", "pft_workbench_gui.py",
    "pft_workbench_i18n.py", "pft_workbench_model.py",
    "pft_workbench_settings.py", "pft_preferences.py", "pft_workbench_tools.py",
    "pft_workbench_views.py",
    "pft_desk_service.py", "pft_desk_controller.py", "pft_desk_qprocess.py",
    "pft_desk_views.py", "pft_desk_prompt.py", "pft_chat_views.py",
    "pft_chat_profile.py", "pft_chat_settings.py", "pft_visual_tools.py", "pft_chat_controller.py", "pft_chat_qprocess.py",
    "pft_document_tools.py", "pft_document_tools_views.py", "pft_panel_host.py",
    "pft_thumbnail.py", "pft_thumbnail_capture.py", "pft_thumbnail_views.py", "pft_appearance_views.py", "pft_appearance_controls.py", "pft_design_inspection.py",
    "Resources/icons/mcp-start.svg", "Resources/icons/mcp-stop.svg",
    "Resources/icons/mcp-status-running.svg", "Resources/icons/mcp-status-stopped.svg",
    "Resources/icons/gear.svg",
    "Resources/icons/document-tools.svg",
    "Resources/icons/axonometric-fit.svg",
    "Resources/icons/appearance.svg",
    "Resources/icons/normalize-names.svg",
    "Resources/icons/link-labels.svg",
    "Resources/icons/part-properties.svg",
    "Resources/icons/desk-tools.svg",
    "Resources/icons/chat-connection.svg",
    "Resources/icons/phoenix-wing-mark.svg",
)
DOCUMENTS = {"LICENSE": "LICENSE", "NOTICE": "docs/ADDON-NOTICE.txt", "README.md": "docs/ADDON-INSTALL.md"}


def read_source(root, relative):
    """Only regular files; never follow a source symlink, including a parent."""
    current = Path(root)
    if current.is_symlink() or not current.is_dir():
        raise ValueError("Source root must be a real directory")
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Source symlink refused: " + relative)
    if not current.is_file():
        raise ValueError("Required package file missing: " + relative)
    data = current.read_bytes()
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("Package file too large: " + relative)
    return data


def build_archive(root=ROOT):
    package = json.loads(read_source(root, "package.json"))
    version = package["version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", version):
        raise ValueError("Invalid package version")
    sources = {name: read_source(root, "addon/" + ADDON + "/" + name) for name in ADDON_FILES}
    metadata = ET.fromstring(sources["package.xml"])
    bridge = ast.parse(sources["pft_bridge.py"])
    versions = [metadata.findtext("{*}version")]
    versions += [ast.literal_eval(node.value) for node in bridge.body
                 if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "ADDON_VERSION" for target in node.targets)]
    server = read_source(root, "src/server.ts").decode("utf-8")
    match = re.search(r"export const VERSION = '([^']+)';", server)
    versions.append(match.group(1) if match else None)
    lock = json.loads(read_source(root, "package-lock.json"))
    versions.extend([lock.get("version"), lock.get("packages", {}).get("", {}).get("version")])
    if len(versions) != 5 or any(value != version for value in versions):
        raise ValueError("Node, addon metadata, bridge and lock versions must agree")
    for target, source in DOCUMENTS.items():
        sources[target] = read_source(root, source)
    manifest = {"schema_version": 1, "addon": ADDON, "version": version,
                "files": {name: sha256(data).hexdigest() for name, data in sorted(sources.items())}}
    sources["MANIFEST.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(sources.items()):
            entry = zipfile.ZipInfo(ADDON + "/" + name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data, compresslevel=9)
    return version, output.getvalue()


def package_addon(output_dir, root=ROOT):
    version, data = build_archive(root)
    filename = "phoenix-freecad-tools-addon-" + version + ".zip"
    checksum = (sha256(data).hexdigest() + "  " + filename + "\n").encode("ascii")
    directory = Path(output_dir)
    if directory.is_symlink():
        raise ValueError("Output directory must not be a symlink")
    outputs = {filename: data, filename + ".sha256": checksum}
    # Check all destinations before creating any; rerunning identical builds is safe.
    for name, content in outputs.items():
        target = directory / name
        if target.is_symlink() or (target.exists() and (not target.is_file() or target.read_bytes() != content)):
            raise ValueError("Existing different output retained: " + name)
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in outputs.items():
        target = directory / name
        try:
            with target.open("xb") as stream:
                stream.write(content)
        except FileExistsError:
            if target.is_symlink() or not target.is_file() or target.read_bytes() != content:
                raise ValueError("Output changed during packaging: " + name) from None
    return filename, checksum.decode("ascii").strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts")
    args = parser.parse_args()
    try:
        filename, checksum = package_addon(args.output_dir)
    except (ValueError, OSError, KeyError, ET.ParseError, SyntaxError) as error:
        parser.exit(1, "Addon package failed: " + str(error) + "\n")
    print(filename)
    print(checksum)


if __name__ == "__main__":
    main()
