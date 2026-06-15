from pathlib import Path


def test_pyproject_declares_pytest_test_extra():
    pyproject = Path("pyproject.toml")
    requirements = Path("requirements.txt")

    assert pyproject.exists(), "pyproject.toml must document installable test dependencies"
    assert requirements.exists(), "requirements.txt must provide a simple setup path"

    pyproject_text = pyproject.read_text(encoding="utf-8")
    requirements_text = requirements.read_text(encoding="utf-8")

    assert 'requires-python = ">=3.9"' in pyproject_text
    assert "[project.optional-dependencies]" in pyproject_text
    assert "test = [" in pyproject_text
    assert '"pytest>=8,<10"' in pyproject_text
    assert ".[test]" in requirements_text
