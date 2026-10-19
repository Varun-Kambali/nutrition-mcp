from nutrition_mcp.models import Food, Source


class UnknownFood(KeyError):
    """Raised when a food ID is not in the registry."""


class Registry:
    """Read-only store of every loaded food and source, keyed by ID."""

    def __init__(self, foods: dict[str, Food], sources: dict[str, Source]) -> None:
        self._foods = dict(foods)
        self._sources = dict(sources)

    def get(self, food_id: str) -> Food:
        try:
            return self._foods[food_id]
        except KeyError:
            raise UnknownFood(food_id) from None

    def foods(self) -> list[Food]:
        return list(self._foods.values())

    def sources(self) -> list[Source]:
        return list(self._sources.values())
