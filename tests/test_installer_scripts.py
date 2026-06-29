import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

WINDOWS_POWERSHELL_REQUIRED = pytest.mark.skipif(
    os.name != "nt" or shutil.which("powershell") is None,
    reason="Windows PowerShell script execution test",
)


@WINDOWS_POWERSHELL_REQUIRED
def test_install_plan_is_per_user_no_admin_and_uses_lasagna_icon(tmp_path: Path) -> None:
    install_dir = tmp_path / "install-root"
    desktop_dir = tmp_path / "desktop"
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "scripts/work_pc/install_lasagna.ps1",
            "-PlanOnly",
            "-ValidateNoAdmin",
            "-InstallDir",
            str(install_dir),
            "-DesktopShortcutDir",
            str(desktop_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    plan = json.loads(result.stdout)
    assert plan["install_dir"] == str(install_dir)
    assert plan["desktop_shortcut"] == str(desktop_dir / "Lasagna.lnk")
    assert plan["icon_path"] == str(install_dir / "assets" / "brand" / "lasagna.ico")
    assert plan["admin_required"] is False
    assert plan["path_mutation"] is False


def test_installer_scripts_do_not_mutate_machine_path() -> None:
    script_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            Path("scripts/work_pc/install_lasagna.ps1"),
            Path("scripts/work_pc/run_lasagna.ps1"),
            Path("scripts/work_pc/run_lasagna_live_batch.ps1"),
            Path("scripts/work_pc/uninstall_lasagna.ps1"),
        )
    )

    assert "[Environment]::SetEnvironmentVariable" not in script_text
    assert "setx" not in script_text.lower()
    assert "Machine" not in script_text
    assert "IconLocation" in script_text


def test_no_shortcut_install_does_not_launch_missing_shortcut() -> None:
    script_text = Path("scripts/work_pc/install_lasagna.ps1").read_text(encoding="utf-8")

    assert "if (-not $NoLaunch -and -not $NoDesktopShortcut)" in script_text


@WINDOWS_POWERSHELL_REQUIRED
def test_live_batch_script_fails_when_python_fails(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python.cmd"
    fake_python.write_text(
        "@echo off\necho fake python failed 1>&2\nexit /b 9\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]

    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "scripts/work_pc/run_lasagna_live_batch.ps1",
            "-ServiceId",
            "IC-123456",
            "-OutputDir",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "Lasagna live batch failed with exit 9" in result.stderr
