# Mahwous Pricing Intelligence - Scraper Engines

## Overview

Complete scraper engine suite for the **Mahwous Smart Pricing System** (`مهووس-للتوزيع`). Supports 10+ competitor sources with unified data model, price monitoring, and intelligent classification.

---

## Engine Architecture

```
engines/
├── __init__.py                      # Unified registry & exports
├── unified_competitor_scraper.py   # Base scraper + 4 Saudi competitors (Noon, Amazon.sa, Jarir, eXtra)
├── perfume_scraper.py               # Niche & Oud specialist (with AI classification)
├── salla_scraper.py                 # Salla platform (Saudi e-commerce)
├── global_scraper.py                # Amazon, FragranceNet, Notino
├── price_monitor.py                 # Continuous price monitoring & alerts
├── mahally_scraper.py               # Production Mahally engine (Algolia API)
├── selenium_scraper_v30.py          # Selenium-based fallback
└── async_scraper.py                 # Async batch scraper
```

---

## Supported Sources

| Source | Type | Class | Status |
|--------|------|-------|--------|
| **Mahally** | Saudi Marketplace | `MahallyScraper` | ✅ Production |
| **Noon** | Saudi Marketplace | `NoonScraper` | ✅ Ready |
| **Amazon.sa** | Global Marketplace | `AmazonSAScraper` | ✅ Ready |
| **Jarir** | Saudi Retail | `JarirScraper` | ✅ Ready |
| **eXtra** | Saudi Electronics | `ExtraScraper` | ✅ Ready |
| **Salla** | Saudi eCommerce | `SallaStoreScraper` | ✅ Ready |
| **Amazon (Global)** | Global | `AmazonScraper` | ✅ Ready |
| **FragranceNet** | Perfume Specialty | `FragranceNetScraper` | ✅ Ready |
| **Notino** | European Perfume | `NotinoScraper` | ✅ Ready |
| **Oud/Attar** | Traditional Perfume | `OudAndAttarScraper` | ✅ Ready |
| **Niche Perfume** | Luxury Fragrance | `NichePerfumeScraper` | ✅ Ready |

---

## Quick Start

### 1. Add a Competitor

```python
from services.scraper_service import ScraperService

service = ScraperService()

# Add Mahally competitor
service.add_competitor("Store Name", "https://mahally.com/stores/12345")

# Add Noon competitor
service.add_noon_competitor("Noon Beauty", "perfume")

# Add Amazon.sa competitor
service.add_amazon_competitor("Amazon Perfume", "oud perfume", marketplace="sa")

# Add Salla store
service.add_salla_competitor("My Salla Store", "https://store.salla.sa")
```

### 2. Scrape All Competitors

```python
# Scrape all active competitors
results = service.scrape_all()

# Scrape only specific sources
results = service.scrape_all(sources=["noon", "amazon_sa"])

# With progress callback
def on_progress(current, total, name):
    print(f"[{current}/{total}] Scraping: {name}")

results = service.scrape_all(progress_cb=on_progress)
```

### 3. Use Unified Scraper Directly

```python
from engines.unified_competitor_scraper import UnifiedScraper

scraper = UnifiedScraper()

# Scrape a single source
products = scraper.scrape(source="noon", category="beauty-and-health", pages=2)

# Scrape multiple sources in parallel
results = scraper.scrape_all(
    targets=["noon", "amazon_sa", "jarir"],
    search="perfume",
    pages=1,
    max_workers=3
)

# Compare prices across sources
comparison = scraper.compare_prices("Oud Wood", ["noon", "amazon_sa"])
```

### 4. Price Monitoring & Alerts

```python
from engines.price_monitor import PriceMonitor, DiscordNotification

monitor = PriceMonitor()

# Add notification channels
monitor.add_channel(DiscordNotification(webhook_url="YOUR_WEBHOOK_URL"))

# Set up product monitoring
monitor.monitor_product("Oud Wood", ["noon", "amazon_sa"], target_price=500.0)

# Process products and detect changes
changes = monitor.process_products(products)

# Generate report
report = monitor.generate_report(days=7)
print(report)
```

### 5. Perfume Classification

```python
from engines.perfume_scraper import PerfumeClassifier

# Auto-classify a perfume from its name
classification = PerfumeClassifier.classify(
    name="Tom Ford Oud Wood EDP 100ml For Men",
    brand="Tom Ford",
    price=850
)

print(classification)
# {
#   "perfume_type": "eau_de_parfum",
#   "size_ml": 100,
#   "gender": "men",
#   "is_niche": True,
#   "is_oud": True,
#   "sillage": "strong",
#   "season": "winter",
#   ...
# }
```

---

## Data Model

All scrapers return `UnifiedProduct` with standard fields:

```python
@dataclass
class UnifiedProduct:
    name: str
    price: float
    original_price: float
    currency: str = "SAR"
    url: str = ""
    image: str = ""
    brand: str = ""
    source: str = ""
    availability: bool = True
    sku: str = ""
    category: str = ""
    description: str = ""
    rating_count: int = 0
    rating_avg: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)
```

---

## Price Monitor Database Schema

```sql
-- Price history
CREATE TABLE price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL,
    source TEXT NOT NULL,
    sku TEXT,
    price REAL NOT NULL,
    original_price REAL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    availability INTEGER DEFAULT 1
);

-- Price alerts
CREATE TABLE price_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL,
    source TEXT NOT NULL,
    url TEXT,
    target_price REAL,
    current_price REAL,
    previous_price REAL,
    alert_type TEXT DEFAULT 'price_drop',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    triggered_at DATETIME,
    is_active INTEGER DEFAULT 1
);

-- Product snapshots
CREATE TABLE product_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL,
    source TEXT NOT NULL,
    sku TEXT,
    data TEXT,  -- JSON
    snapshot_date DATE DEFAULT CURRENT_DATE
);
```

---

## Configuration

### Environment Variables

```bash
# Price Monitor
PRICE_MONITOR_DB=data/price_monitor.db
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Scraping
SCRAPE_DELAY=1.5
MAX_RETRIES=3
TIMEOUT=20
```

### Competitor JSON Format

```json
[
  {
    "name": "Mahally Store",
    "store_url": "https://mahally.com/stores/12345",
    "mahally_store_id": 12345,
    "source": "mahally",
    "source_config": {},
    "is_active": true
  },
  {
    "name": "Noon Beauty",
    "store_url": "",
    "mahally_store_id": 0,
    "source": "noon",
    "source_config": {
      "search_term": "perfume",
      "category": "beauty-and-health"
    },
    "is_active": true
  }
]
```

---

## Running Tests

```bash
# Test unified scraper
python -m engines.unified_competitor_scraper

# Test perfume classifier
python -m engines.perfume_scraper

# Test Salla scraper
python -m engines.salla_scraper

# Test price monitor
python -m engines.price_monitor

# Test global scraper
python -m engines.global_scraper
```

---

## Development Notes

- All scrapers extend `BaseCompetitorScraper` with retry, delay, and proxy support
- `UnifiedScraper` orchestrates parallel scraping via `ThreadPoolExecutor`
- `PerfumeClassifier` uses regex patterns for intelligent product classification
- `PriceMonitor` detects: price drops, price rises, back-in-stock, out-of-stock, new products
- Price anomalies are detected using statistical deviation (±2 std from mean)

---

## License

Proprietary - Mahwous Pricing Intelligence System
