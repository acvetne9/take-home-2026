"""Ingestion entry point: product detail pages in, validated `Product` records out.

    python main.py                      # every page in ./data -> ./out/products.json
    python main.py --no-ai              # deterministic only -> ./out/products.no-ai.json
    python main.py data/one.html        # a single page
    python main.py --model openai/gpt-5-mini

Pages are processed concurrently: the work is I/O-bound on the model calls, and the parsing is
independent per page, so there is nothing to serialise.
"""

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from pathlib import Path

import ai
from extract.dom import Document, UndecodableDocument
from extract.pipeline import extract, extract_deterministic, _confidence, _page_warnings
from extract import taxonomy as taxonomy_mod

logger = logging.getLogger("ingest")

DATA_DIR = Path(__file__).parent / "data"
OUT_DIR = Path(__file__).parent / "out"
# Enough concurrency to hide model latency, low enough to stay well inside rate limits.
CONCURRENCY = 5


class UsageMeter(logging.Handler):
    """Read token usage back out of `ai._log_usage` rather than duplicating its accounting.

    `ai.py` already prints exactly what we need and stays in the loop for every call, so we
    listen to it instead of re-instrumenting the client.
    """

    LINE = re.compile(r"Token usage for (\S+): input=(\d+), output=(\d+), reasoning=(\d+)")

    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, int, int, int]] = []

    def emit(self, record):
        m = self.LINE.search(record.getMessage())
        if m:
            self.calls.append((m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))))

    def report(self, products: int) -> str:
        if not self.calls or not products:
            return "no model calls"
        total = 0.0
        tokens_in = tokens_out = 0
        for model, in_tok, out_tok, _reasoning in self.calls:
            prices = ai.MODEL_PRICES.get(model, {"input": 0.0, "output": 0.0})
            total += in_tok / 1e6 * prices["input"] + out_tok / 1e6 * prices["output"]
            tokens_in += in_tok
            tokens_out += out_tok
        per_product = total / products
        return (
            f"{len(self.calls)} calls over {products} products "
            f"({len(self.calls) / products:.1f}/product)\n"
            f"  tokens/product : {tokens_in / products:,.0f} in, {tokens_out / products:,.0f} out\n"
            f"  cost           : ${per_product:.6f}/product  "
            f"${per_product * 1e6:,.0f}/1M  ${per_product * 50e6:,.0f}/50M"
        )


async def run_one(path: Path, args, semaphore) -> dict:
    async with semaphore:
        started = time.perf_counter()
        try:
            doc = Document.from_path(path)
        except UndecodableDocument as exc:
            logger.error("%s: %s", path.name, exc)
            return {"source": path.name, "error": str(exc)}

        if args.no_ai:
            # The deterministic half on its own, so the extractor can be exercised, diffed and
            # regression-tested without spending anything or depending on a network.
            raw = extract_deterministic(doc)
            warnings = _page_warnings(raw)
            query = taxonomy_mod.retrieval_query(
                raw.fields.name, raw.fields.brand, raw.fields.breadcrumb, raw.fields.description)
            from extract.pipeline import _assemble, UnpriceablePage
            try:
                product = _assemble(raw, None, taxonomy_mod.lexical_only(query), 0)
            except UnpriceablePage:
                product = None
            result = {"product": product.model_dump(mode="json") if product else None,
                      "confidence": _confidence(raw, warnings), "warnings": warnings,
                      "field_sources": raw.fields.sources, "url": doc.canonical_url()}
        else:
            extracted = await extract(doc, ai, model=args.model, allow_cascade=not args.no_cascade)
            result = extracted.model_dump(mode="json")

        result["source"] = path.name
        result["elapsed_s"] = round(time.perf_counter() - started, 2)
        return result


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="HTML files (default: everything in ./data)")
    parser.add_argument("--no-ai", action="store_true", help="deterministic stages only")
    parser.add_argument("--no-cascade", action="store_true", help="never escalate to a larger model")
    parser.add_argument("--model", help="pin a single model instead of the cascade")
    # Deliberately defaulted late, not here: a --no-ai run must not be able to overwrite the
    # catalog that ships and that the deployed API serves. Its output is plausible — real
    # prices and images, but lexically guessed categories, no key features and a hard-coded
    # confidence — so a wrong file looks exactly like a right one. It reached out/ twice
    # before this, once via `verify_all.py`, which runs `--no-ai` as its no-spend step.
    parser.add_argument("--out", help="output path (default: ./out/products[.no-ai].json)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    meter = UsageMeter()
    logging.getLogger("ai").addHandler(meter)

    paths = [Path(p) for p in args.paths] or sorted(DATA_DIR.glob("*.html"))
    if not paths:
        logger.error("no input files")
        return 1

    started = time.perf_counter()
    semaphore = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*(run_one(p, args, semaphore) for p in paths))
    elapsed = time.perf_counter() - started

    default_name = "products.no-ai.json" if args.no_ai else "products.json"
    out_path = Path(args.out) if args.out else OUT_DIR / default_name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n{'page':16} {'conf':>5} {'price':>10} {'imgs':>5} {'vars':>5} {'axes':>5}  category")
    print("-" * 104)
    extracted = 0
    for r in results:
        product = r.get("product")
        if not product:
            print(f"{r['source']:16} {r.get('confidence', 0):>5.2f} {'REFUSED':>10}   "
                  f"{'; '.join(r.get('warnings', []))[:60]}")
            continue
        extracted += 1
        price = product["price"]
        print(f"{r['source']:16} {r['confidence']:>5.2f} "
              f"{price['currency']} {price['price']:>7,.2f} "
              f"{len(product['image_urls']):>5} {len(product['variants']):>5} "
              f"{len(product['options']):>5}  {product['category']['name'][:46]}")
    print("-" * 104)
    print(f"{extracted}/{len(results)} extracted in {elapsed:.1f}s -> {out_path}")
    print(meter.report(extracted))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
