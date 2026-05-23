#!/usr/bin/env python3
"""
Patches all OpenMRS modules (omods) in the distribution and data module
directories to remove <aware_of_module> legacyui entries from their
config.xml files.

Background
----------
OpenMRS treats <aware_of_modules> entries as soft startup-ordering edges
in its dependency graph. When legacyui fails to start (which happens in
the TenaOS runtime because its Tomcat filter registration conflicts
with the already-initialised context), every module that is "aware of" it
fails the topological sort and is left un-started.  This cascades: queue,
emrapi, initializer, reporting, cohort, and many others are all "aware of"
legacyui, so none of them start — even though they have no hard runtime
dependency on it.

The patch removes the awareness edges from all omods, not just
webservices.rest, so that each module starts independently of legacyui's
status.  legacyui itself remains in the modules folder for any optional
features that use it at runtime; we are only removing the startup-ordering
constraint.
"""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import os
import re
import shutil
import tempfile


MODULE_DIRS = [
    Path("/opt/openmrs/distribution/openmrs_modules"),
    Path("/opt/openmrs/data/modules"),
]

# Strips the entire <aware_of_modules> block when the only or last entry is
# legacyui. Works even when there is surrounding whitespace or newlines.
LEGACY_UI_AWARENESS_RE = re.compile(
    r"\s*<aware_of_modules>\s*"
    r"<aware_of_module>\s*org\.openmrs\.module\.legacyui\s*</aware_of_module>\s*"
    r"</aware_of_modules>\s*",
    re.S,
)

# When legacyui is one of several entries, strip only that line.
LEGACY_UI_ENTRY_RE = re.compile(
    r"\s*<aware_of_module>\s*org\.openmrs\.module\.legacyui\s*</aware_of_module>",
    re.S,
)


def patch_omod(path: Path) -> bool:
    with ZipFile(path, "r") as src:
        entries = [(info, src.read(info.filename)) for info in src.infolist()]

    changed = False
    fd, tmp_name = tempfile.mkstemp(suffix=".omod")
    os.close(fd)
    tmp_path = Path(tmp_name)

    try:
        with ZipFile(tmp_path, "w", ZIP_DEFLATED) as dst:
            for info, data in entries:
                if info.filename == "config.xml":
                    text = data.decode("utf-8")
                    # First try: remove entire <aware_of_modules> block.
                    patched = LEGACY_UI_AWARENESS_RE.sub("\n", text)
                    # Second try: legacyui is one among multiple entries.
                    if patched == text:
                        patched = LEGACY_UI_ENTRY_RE.sub("", text)
                    if patched != text:
                        changed = True
                        data = patched.encode("utf-8")
                dst.writestr(info, data)

        if changed:
            shutil.copyfile(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)

    return changed


def main() -> None:
    patched = []
    for module_dir in MODULE_DIRS:
        if not module_dir.exists():
            continue
        for omod in sorted(module_dir.glob("*.omod")):
            if patch_omod(omod):
                patched.append(str(omod))

    if patched:
        print(f"Patched {len(patched)} module(s) — removed legacyui startup-ordering constraint:")
        for path in patched:
            print(f"  - {path}")
    else:
        print("No legacyui startup-ordering patches were needed.")


if __name__ == "__main__":
    main()
