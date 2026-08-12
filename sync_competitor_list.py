"""Build the scraper-facing competitor list from the persistent competitor database."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path


def sync_competitor_list(data_dir: Path) -> dict[str, int | str]:
    """Persist every configured or already-imported competitor as one JSON entry.

    The authoritative market dataset is ``pricing_v18.db``.  The legacy JSON file
    can be a short, manually maintained subset; rebuilding it here prevents the
    scraper and UI from silently reverting to that subset after a deployment.
    """
    data_dir = Path(data_dir)
    database = data_dir / "pricing_v18.db"
    if not database.exists():
        raise FileNotFoundError(f"competitor database not found: {database}")

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        product_rows = {
            str(row["competitor"]): dict(row)
            for row in connection.execute(
                "SELECT competitor, COUNT(*) AS total_products, MAX(updated_at) AS last_scraped "
                "FROM competitor_products_store "
                "WHERE TRIM(COALESCE(competitor, '')) <> '' GROUP BY competitor"
            ).fetchall()
        }
        configured_rows = {
            str(row["name"]): dict(row)
            for row in connection.execute(
                "SELECT name, domain, is_active, added_at FROM competitors "
                "WHERE TRIM(COALESCE(name, '')) <> ''"
            ).fetchall()
        }
    finally:
        connection.close()

    names = sorted(set(product_rows) | set(configured_rows), key=str.casefold)
    entries: list[dict[str, object]] = []
    for name in names:
        product = product_rows.get(name, {})
        configured = configured_rows.get(name, {})
        domain = str(configured.get("domain") or "").strip()
        store_url = domain if domain.startswith(("http://", "https://")) else (
            f"https://{domain}" if domain else ""
        )
        entries.append({
            "name": name,
            "store_url": store_url,
            "sitemap_url": "",
            "mahally_store_id": None,
            "total_products": int(product.get("total_products") or 0),
            "last_scraped": str(product.get("last_scraped") or configured.get("added_at") or ""),
            "is_active": bool(configured.get("is_active", name in product_rows)),
            "source": "pricing_v18.db",
        })

    target = data_dir / "competitors_list_v30.json"
    temporary = target.with_name(f".{target.name}.partial")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(entries, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "status": "synced",
        "entries": len(entries),
        "with_product_data": sum(1 for entry in entries if int(entry["total_products"]) > 0),
        "active_entries": sum(1 for entry in entries if bool(entry["is_active"])),
    }
