class Inventory:
    def __init__(self):
        self.items = []

    def add(self, name, price):
        self.items.append({"name": name, "price": price})

    def total(self):
        return sum(item["price"] for item in self.items)
