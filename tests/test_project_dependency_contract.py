from pathlib import Path
import tomllib


def test_pyproject_declares_pytest_test_extra():
    pyproject = Path("pyproject.toml")
    requirements = Path("requirements.txt")

    assert pyproject.exists(), "pyproject.toml must document installable test dependencies"
    assert requirements.exists(), "requirements.txt must provide a simple setup path"

    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    test_deps = data["project"]["optional-dependencies"]["test"]

    assert any(dep.lower().startswith("pytest") for dep in test_deps)
    assert ".[test]" in requirements.read_text(encoding="utf-8")
