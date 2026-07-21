from pathlib import Path


def test_assistant_core_has_no_whatsapp_or_kapso_dependency() -> None:
    assistant_files = Path("app/assistant").rglob("*.py")
    forbidden = ("kapso", "whatsapp")

    for file_path in assistant_files:
        content = file_path.read_text(encoding="utf-8").lower()
        assert all(term not in content for term in forbidden), file_path
