from typing import Any
from enum import Enum
from pathlib import Path
from pydantic import BaseModel, Field, field_validator

# Load categories once at module level
CATEGORIES_FILE = Path(__file__).parent / "categories.txt"
VALID_CATEGORIES = set()
if CATEGORIES_FILE.exists():
    with open(CATEGORIES_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                VALID_CATEGORIES.add(line)

class Category(BaseModel):
    # A category from Google's Product Taxonomy
    # https://www.google.com/basepages/producttype/taxonomy.en-US.txt
    name: str

    @field_validator("name")
    @classmethod
    def validate_name_exists(cls, v: str) -> str:
        if v not in VALID_CATEGORIES:
            raise ValueError(f"Category '{v}' is not a valid category in categories.txt")
        return v

class Price(BaseModel):
    price: float
    currency: str
    # If a product is on sale, this is the original price
    compare_at_price: float | None = None


# ---------------------------------------------------------------------------------------
# Variants
#
# A variant is "a discrete configuration/selection of the product". Two shapes are needed,
# not one, because a PDP asks two different questions of the same data:
#
#   "What can I choose?"        -> the axes.   Size: [S, M, L], Color: [Iron, Navy]
#   "What happens if I choose?" -> the SKUs.   Iron/M is £170, in stock, has its own photos
#
# A flat list of strings (`["S","M","L"]`) cannot express a product that varies by both fit
# and size, and loses per-SKU price and stock entirely. A list of SKUs alone forces every
# consumer to re-derive the axes before it can render a picker, and gets verbose fast. So we
# publish both: `Product.options` drives the picker, `Product.variants` carries the facts.
#
# `options` is a `dict[str, str]` rather than an enum on purpose: it generalises to axes we
# have never seen (voltage, scent, length) without a schema change, and it round-trips to both
# schema.org (`variesBy`/`hasVariant`) and the common platform shape (`options`/`variants`),
# which are the two formats the rest of the industry speaks.
# ---------------------------------------------------------------------------------------

class Availability(str, Enum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    PREORDER = "preorder"
    DISCONTINUED = "discontinued"
    UNKNOWN = "unknown"


class VariantOption(BaseModel):
    """One axis of choice, e.g. Size -> [S, M, L]. Ordered as the page presents it."""
    name: str
    values: list[str]


class Variant(BaseModel):
    """One concrete, purchasable configuration of the product."""
    sku: str | None = None
    options: dict[str, str] = Field(default_factory=dict)
    price: Price | None = None          # variants genuinely differ in price (a tall fit, a larger size)
    image_urls: list[str] = Field(default_factory=list)
    availability: Availability = Availability.UNKNOWN
    # The blocking key for entity resolution: a GTIN identifies the same physical item across
    # every retailer selling it, which is what makes a product *graph* a graph rather than a
    # pile of scraped pages. When a page states one, it is the most valuable field on the row.
    gtin: str | None = None
    mpn: str | None = None


# This is the final product schema that you need to output. 
# You may add additional models as needed.
class Product(BaseModel):
    name: str
    price: Price
    description: str
    key_features: list[str]
    image_urls: list[str]
    video_url: str | None = None
    category: Category
    brand: str
    colors: list[str]
    variants: list[Any] = Field(default_factory=list)   # list[Variant]; see above
    # Added beside `variants`, not instead of it: the axes a shopper picks from.
    options: list[VariantOption] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    """A `Product` plus what we know about how well we did — the part an ingestion pipeline
    needs in order to decide whether to trust the row, re-extract it, or queue it for review."""
    product: Product | None = None
    url: str | None = None
    source: str | None = None
    confidence: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    field_sources: dict[str, str] = Field(default_factory=dict)
