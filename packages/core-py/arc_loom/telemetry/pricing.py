from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    input_per_1m: float
    output_per_1m: float
    cache_read_per_1m: float = 0.0
    cache_write_per_1m: float = 0.0


DEFAULT_MODEL_PRICES: dict[str, ModelPrice] = {
    "anthropic/claude-opus-4.5": ModelPrice(
        input_per_1m=15.0,
        output_per_1m=75.0,
        cache_read_per_1m=1.5,
        cache_write_per_1m=18.75,
    ),
    "fable-5.1": ModelPrice(
        input_per_1m=12.0,
        output_per_1m=60.0,
        cache_read_per_1m=1.2,
        cache_write_per_1m=15.0,
    ),
    "openai/gpt-4o": ModelPrice(
        input_per_1m=2.5,
        output_per_1m=10.0,
        cache_read_per_1m=1.25,
        cache_write_per_1m=2.5,
    ),
    "local/llama-3.2-1b": ModelPrice(
        input_per_1m=0.02,
        output_per_1m=0.02,
    ),
    "llama-3.2-1b": ModelPrice(
        input_per_1m=0.02,
        output_per_1m=0.02,
    ),
}


class PricingCatalog:
    """Token cost calculator across frontier Source/Builder and local Target 1B roles."""

    def __init__(self, custom_prices: dict[str, ModelPrice] | None = None) -> None:
        self._prices: dict[str, ModelPrice] = dict(DEFAULT_MODEL_PRICES)
        if custom_prices:
            self._prices.update(custom_prices)

    def register_model(self, model_name: str, price: ModelPrice) -> None:
        self._prices[model_name] = price

    def get_price(self, model_name: str) -> ModelPrice:
        if model_name in self._prices:
            return self._prices[model_name]
        if model_name.startswith("local/"):
            return ModelPrice(input_per_1m=0.02, output_per_1m=0.02)
        return ModelPrice(input_per_1m=5.0, output_per_1m=15.0)

    def calculate_cost_usd(
        self,
        model_name: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        price = self.get_price(model_name)
        total = (
            (input_tokens * price.input_per_1m)
            + (output_tokens * price.output_per_1m)
            + (cache_read_tokens * price.cache_read_per_1m)
            + (cache_write_tokens * price.cache_write_per_1m)
        ) / 1_000_000.0
        return round(total, 8)
