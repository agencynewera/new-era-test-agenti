from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Prodotto:
    sku: str
    title: str
    price: float
    quantity: int
    product_type: str
    ean: str = ""
    brand: str = ""
    description: str = ""
    category: str = ""
    images: list[str] = field(default_factory=list)
    condition: str = "New"
    bullet_points: list[str] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)
    weight: float | None = None
    dimensions: dict | None = None
    mpn: str = ""
