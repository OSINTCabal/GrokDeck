import subprocess
from pathlib import Path


def test_installer_utility_arguments_do_not_start_installation():
    script = Path(__file__).resolve().parents[1] / "packaging/install.sh"
    for option, code in [("--help", 0), ("--unknown", 2)]:
        result = subprocess.run(["bash", str(script), option], capture_output=True)
        assert result.returncode == code
