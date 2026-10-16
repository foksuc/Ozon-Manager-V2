"""Run the real PowerShell path preflight against synthetic Windows trees."""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap")
@pytest.mark.parametrize("linked", [False, True])
def test_native_bootstrap_preflight_nested_junction(tmp_path, linked):
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    nested = project / "frontend" / ".hidden" / "nested"
    nested.mkdir(parents=True)
    (nested / "file.txt").write_text("synthetic", encoding="utf-8")
    source = (Path(__file__).resolve().parents[2] / "scripts/lifecycle.ps1").read_text(encoding="utf-8")
    preflight = source.split("foreach ($Config", 1)[0]
    assert "Diagnostics.Process" not in preflight
    script = scripts / "preflight.ps1"
    script.write_text(preflight + "Write-Host 'PREFLIGHT_PASSED'\n", encoding="utf-8")
    external = tmp_path / "outside"
    external.mkdir()
    link = nested / "planted"
    system = Path(os.environ["SystemRoot"]) / "System32"
    if linked:
        subprocess.run([str(system / "cmd.exe"), "/d", "/c", "mklink", "/J", str(link), str(external)], check=True, capture_output=True)
    try:
        result = subprocess.run([str(system / "WindowsPowerShell/v1.0/powershell.exe"), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Action", "start"], capture_output=True, text=True, timeout=30)
        if linked:
            assert result.returncode != 0
            assert "Nested reparse point denied" in result.stderr
            assert "PREFLIGHT_PASSED" not in result.stdout
        else:
            assert result.returncode == 0, result.stderr
            assert "PREFLIGHT_PASSED" in result.stdout
    finally:
        if linked:
            assert link.is_relative_to(project) and link.is_junction()
            link.rmdir()  # Remove only this verified fixture junction, never its target.
