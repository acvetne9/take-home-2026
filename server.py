"""Read-only API over the extracted catalog.

Deliberately thin. There is no database: the server's whole job is to load what the ingestion
run wrote and serve it. Extraction and serving stay decoupled, which is also how you would want
them at scale, just with a real store in the middle instead of a JSON file.

    python server.py            # http://127.0.0.1:8000  (docs at /docs)

A catalog endpoint that is cheap to page over, and a detail endpoint that returns the whole
record. Filtering is by exact facet rather than free text, because the facets are the part we
extracted deterministically and can therefore stand behind.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from models import Product

logger = logging.getLogger("server")

CATALOG = Path(__file__).parent / "out" / "products.json"


class CatalogEntry(BaseModel):
    """A product plus the provenance a consumer needs in order to trust it.

    The confidence and warnings are not debug output. An agent deciding whether to quote a
    price, and a human reviewing an ingestion run, both need to know that this row's price came
    from a schema.org annotation rather than from a regex over rendered text.
    """
    id: str
    product: Product
    url: str | None = None
    source: str | None = None
    confidence: float = 0.0
    warnings: list[str] = []
    field_sources: dict[str, str] = {}


def _slug(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text]
    return "-".join(filter(None, "".join(keep).split("-")))[:60]


def load_catalog(path: Path = CATALOG) -> list[CatalogEntry]:
    if not path.exists():
        logger.warning("%s not found - run `python main.py` first", path)
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    entries: list[CatalogEntry] = []
    for row in rows:
        product = row.get("product")
        if not product:
            # A refusal is a legitimate outcome, not an error; it just does not get published.
            logger.info("skipping %s: %s", row.get("source"), "; ".join(row.get("warnings", [])))
            continue
        entries.append(CatalogEntry(
            id=_slug(f"{product['brand']}-{product['name']}") or _slug(row.get("source", "")),
            product=Product(**product),
            url=row.get("url"),
            source=row.get("source"),
            confidence=row.get("confidence", 0.0),
            warnings=row.get("warnings", []),
            field_sources=row.get("field_sources", {}),
        ))
    return entries


app = FastAPI(title="Product catalog", version="1.0.0",
              description="Structured products extracted from raw product detail pages.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_CATALOG: list[CatalogEntry] = []


@app.on_event("startup")
def _startup() -> None:
    global _CATALOG
    _CATALOG = load_catalog()
    logger.info("loaded %d products", len(_CATALOG))


class CatalogResponse(BaseModel):
    total: int
    brands: list[str]
    categories: list[str]
    products: list[CatalogEntry]


@app.get("/api/products", response_model=CatalogResponse)
def list_products(
    brand: str | None = Query(None, description="Exact brand match"),
    category: str | None = Query(None, description="Category path prefix"),
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> CatalogResponse:
    rows = _CATALOG
    if brand:
        rows = [e for e in rows if e.product.brand.lower() == brand.lower()]
    if category:
        rows = [e for e in rows if e.product.category.name.startswith(category)]
    return CatalogResponse(
        total=len(rows),
        # Facets are computed over the whole catalog, not the filtered page, so the filter
        # controls do not disappear as soon as you use one.
        brands=sorted({e.product.brand for e in _CATALOG if e.product.brand}),
        categories=sorted({e.product.category.name for e in _CATALOG}),
        products=rows[offset:offset + limit],
    )


@app.get("/api/products/{product_id}", response_model=CatalogEntry)
def get_product(product_id: str) -> CatalogEntry:
    for entry in _CATALOG:
        if entry.id == product_id:
            return entry
    raise HTTPException(status_code=404, detail=f"No product with id {product_id!r}")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "products": len(_CATALOG), "catalog": str(CATALOG)}


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="127.0.0.1", port=8000)
