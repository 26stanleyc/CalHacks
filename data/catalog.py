"""Hand-made product catalog (R2, spec 3.1). An Apify scrape of real listings can replace
catalog.json later; anything that loads the catalog only depends on `load_catalog()`.

Regenerate catalog.json (deterministic):

    uv run python -m data.catalog
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from contracts.schemas import Product

CATALOG_PATH = Path(__file__).resolve().parent / "catalog.json"

# Honest storefronts, by category. Malicious merchants are attackers, not catalog entries.
MERCHANTS: dict[str, list[str]] = {
    "electronics": ["m-voltmart", "m-circuitcity"],
    "phone_accessories": ["m-voltmart", "m-caseworks"],
    "home_office": ["m-deskdepot", "m-workwell"],
    "kitchen": ["m-panandpantry", "m-homegoods"],
    "groceries": ["m-freshcart", "m-panandpantry"],
    "books": ["m-pageturner", "m-readmore"],
    "clothing": ["m-threadline", "m-outfitters"],
    "beauty": ["m-glowshop", "m-homegoods"],
    "toys": ["m-playland", "m-readmore"],
    "sports": ["m-trailhead", "m-outfitters"],
    "pet_supplies": ["m-pawpantry", "m-freshcart"],
    "software": ["m-saasly", "m-devtools"],
    "cloud_compute": ["m-cloudnine", "m-devtools"],
    "travel": ["m-tripwise", "m-farefinder"],
    "gift_cards": ["m-giftly", "m-voltmart"],
    "digital_media": ["m-streambox", "m-pageturner"],
    "office_supplies": ["m-deskdepot", "m-paperclip"],
    "health": ["m-wellrx", "m-glowshop"],
}

# (title, price in cents, variants). Each variant becomes one product.
ITEMS: dict[str, list[tuple[str, int, list[str]]]] = {
    "electronics": [
        ("Wireless earbuds", 4_999, ["black", "white", "noise cancelling"]),
        ("Bluetooth speaker", 3_999, ["mini", "waterproof", "party"]),
        ("27-inch monitor", 21_999, ["1440p", "4K"]),
        ("Mechanical keyboard", 8_999, ["tenkeyless", "full size", "low profile"]),
        ("Webcam", 5_999, ["1080p", "4K"]),
        ("Portable SSD", 8_999, ["1TB", "2TB"]),
    ],
    "phone_accessories": [
        ("USB-C charger", 1_999, ["20W", "45W", "65W"]),
        ("Phone case", 1_499, ["clear", "rugged", "leather"]),
        ("Screen protector", 999, ["2-pack", "privacy"]),
        ("Power bank", 2_999, ["10,000 mAh", "20,000 mAh"]),
        ("USB-C cable", 899, ["1 m", "2 m", "braided 3 m"]),
    ],
    "home_office": [
        ("Ergonomic office chair", 24_999, ["mesh", "leather"]),
        ("Standing desk", 39_999, ["48-inch", "60-inch"]),
        ("Desk lamp", 3_499, ["LED", "clamp"]),
        ("Monitor arm", 6_999, ["single", "dual"]),
        ("Cable management kit", 1_999, ["basic", "deluxe"]),
    ],
    "kitchen": [
        ("Chef's knife", 4_999, ["8-inch", "Santoku"]),
        ("Nonstick frying pan", 3_499, ["10-inch", "12-inch"]),
        ("Coffee grinder", 3_999, ["blade", "burr"]),
        ("French press", 2_999, ["small", "large"]),
        ("Glass food containers", 2_499, ["10-piece", "18-piece"]),
    ],
    "groceries": [
        ("Ground coffee", 1_299, ["medium roast", "dark roast", "decaf"]),
        ("Olive oil", 1_599, ["500 ml", "1 l"]),
        ("Granola", 699, ["honey almond", "dark chocolate"]),
        ("Green tea", 899, ["40 bags", "loose leaf"]),
        ("Sparkling water", 599, ["12-pack lime", "12-pack plain"]),
        ("Pasta", 399, ["spaghetti", "penne", "fusilli"]),
    ],
    "books": [
        ("Paperback novel", 1_699, ["mystery", "science fiction", "literary"]),
        ("Cookbook", 2_999, ["weeknight", "baking"]),
        ("Programming handbook", 4_499, ["Python", "TypeScript", "Rust"]),
        ("Children's picture book", 1_299, ["animals", "bedtime"]),
    ],
    "clothing": [
        ("Cotton T-shirt", 1_999, ["S", "M", "L", "XL"]),
        ("Rain jacket", 8_999, ["men's", "women's"]),
        ("Wool socks", 1_499, ["3-pack", "6-pack"]),
        ("Running shoes", 11_999, ["men's", "women's"]),
    ],
    "beauty": [
        ("Moisturizer", 2_499, ["day", "night"]),
        ("Sunscreen SPF 50", 1_599, ["lotion", "spray"]),
        ("Shampoo", 1_199, ["volumizing", "sensitive scalp"]),
        ("Electric toothbrush", 5_999, ["basic", "pro"]),
    ],
    "toys": [
        ("Building blocks set", 3_999, ["500 pieces", "1,000 pieces"]),
        ("Board game", 3_499, ["strategy", "family", "party"]),
        ("Jigsaw puzzle", 1_999, ["500 pieces", "1,000 pieces"]),
        ("Remote control car", 4_999, ["off-road", "drift"]),
    ],
    "sports": [
        ("Yoga mat", 2_999, ["4 mm", "6 mm"]),
        ("Adjustable dumbbells", 19_999, ["25 lb", "50 lb"]),
        ("Water bottle", 2_499, ["24 oz", "32 oz"]),
        ("Camping tent", 14_999, ["2-person", "4-person"]),
        ("Bike helmet", 5_999, ["road", "mountain"]),
    ],
    "pet_supplies": [
        ("Dry dog food", 4_599, ["15 lb", "30 lb"]),
        ("Cat litter", 1_899, ["clumping", "unscented"]),
        ("Chew toys", 1_299, ["3-pack", "durable"]),
        ("Pet bed", 3_999, ["medium", "large"]),
    ],
    "software": [
        ("Password manager, annual", 3_599, ["personal", "family"]),
        ("Photo editor license", 9_999, ["standard", "pro"]),
        ("VPN, annual", 5_999, ["1 device", "5 devices"]),
        ("Note-taking app, annual", 4_999, ["personal", "team"]),
    ],
    "cloud_compute": [
        ("GPU compute credits", 5_000, ["$50", "$100"]),
        ("Object storage, monthly", 1_000, ["100 GB", "1 TB"]),
        ("API calls bundle", 2_000, ["10k calls", "100k calls"]),
        ("Managed database, monthly", 2_500, ["starter", "standard"]),
    ],
    "travel": [
        ("Economy flight", 18_999, ["domestic", "regional"]),
        ("Hotel night", 14_999, ["standard room", "king room"]),
        ("Airport shuttle", 3_500, ["one way", "round trip"]),
        ("Train ticket", 6_500, ["standard", "first class"]),
        ("Travel insurance", 4_200, ["single trip", "annual"]),
    ],
    "gift_cards": [
        ("Digital gift card", 2_500, ["$25", "$50", "$100"]),
        ("Restaurant gift card", 5_000, ["$50", "$100"]),
    ],
    "digital_media": [
        ("Movie rental", 499, ["HD", "4K"]),
        ("Ebook", 999, ["bestseller", "classic"]),
        ("Music streaming, monthly", 1_099, ["individual", "family"]),
        ("Audiobook", 1_499, ["fiction", "nonfiction"]),
    ],
    "office_supplies": [
        ("Printer paper", 899, ["500 sheets", "2,500 sheets"]),
        ("Gel pens", 799, ["12-pack", "24-pack"]),
        ("Sticky notes", 599, ["12 pads", "24 pads"]),
        ("Toner cartridge", 6_999, ["black", "color set"]),
        ("Notebooks", 1_199, ["3-pack", "dotted"]),
    ],
    "health": [
        ("Multivitamins", 1_999, ["60 count", "120 count"]),
        ("First aid kit", 2_499, ["home", "travel"]),
        ("Digital thermometer", 1_499, ["oral", "forehead"]),
        ("Allergy relief", 1_299, ["24 count", "60 count"]),
    ],
}

# Variant names that set the price directly, e.g. a "$100" gift card costs $100.
_DOLLAR_VARIANT = re.compile(r"^\$(\d+)$")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def build_catalog() -> list[Product]:
    products: list[Product] = []
    for category, items in ITEMS.items():
        merchants = MERCHANTS[category]
        for title, price, variants in items:
            for v_index, variant in enumerate(variants):
                dollar = _DOLLAR_VARIANT.match(variant)
                # Bigger variants cost a bit more; this keeps prices realistic without a table per item.
                base = int(dollar.group(1)) * 100 if dollar else int(round(price * (1 + 0.25 * v_index), -1)) - 1
                # Both stores in the category list it; the second one prices it a little differently.
                for m_index, merchant in enumerate(merchants):
                    price_cents = base if dollar or m_index == 0 else int(round(base * (0.95 + 0.04 * v_index), -1)) - 1
                    products.append(Product(
                        product_id=f"prod-{len(products) + 1:04d}", merchant_id=merchant,
                        title=f"{title} ({variant})", category=category, price_cents=price_cents,
                        page_url=f"https://{merchant.removeprefix('m-')}.shop.example/p/{_slug(title)}-{_slug(variant)}",
                    ))
    return products


@lru_cache(maxsize=1)
def load_catalog() -> tuple[Product, ...]:
    return tuple(Product.model_validate(p) for p in json.loads(CATALOG_PATH.read_text()))


def products_by_category() -> dict[str, list[Product]]:
    out: dict[str, list[Product]] = {}
    for p in load_catalog():
        out.setdefault(p.category, []).append(p)
    return out


def write_catalog(path: Path = CATALOG_PATH) -> int:
    products = build_catalog()
    path.write_text(json.dumps([p.model_dump(mode="json") for p in products], indent=1) + "\n")
    return len(products)


if __name__ == "__main__":
    print(f"wrote {write_catalog()} products to {CATALOG_PATH.name}")
