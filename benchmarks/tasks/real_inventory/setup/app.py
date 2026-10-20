from store import Inventory


def restock(inventory, name, price, qty):
    inventory.add(name, price)
    return inventory.total()
