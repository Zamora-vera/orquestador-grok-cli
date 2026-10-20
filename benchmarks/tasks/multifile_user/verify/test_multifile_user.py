from models import User
from service import display


def test_display():
    assert display(User("Ada", "Lovelace")) == "Ada Lovelace"
