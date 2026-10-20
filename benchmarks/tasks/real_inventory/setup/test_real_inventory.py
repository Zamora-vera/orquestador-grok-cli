from app import restock
from store import Inventory


def test_total():
    inventory = Inventory()
    inventory.add("a", 2)
    inventory.add("b", 3)
    assert inventory.total() == 5
    restock(inventory, "c", 4, 3)
    assert inventory.total() == 17
