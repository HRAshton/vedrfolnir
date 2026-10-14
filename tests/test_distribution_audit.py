"""Distribution audit rejects path traversal and accidentally included private state."""
import pytest
from scripts.qualify_packages import audit_members


@pytest.mark.parametrize("name", ["../secret", "/etc/passwd", "src/.env", "state.sqlite3",
                                     "src/model.pkl", "datasets/private.csv", ".git/config"])
def test_private_or_unsafe_members_rejected(name):
    with pytest.raises(AssertionError):
        audit_members([name])


def test_public_resources_allowed():
    audit_members(["vedrfolnir/static/dashboard.html", "src/vedrfolnir/__init__.py", "LICENSE"])
