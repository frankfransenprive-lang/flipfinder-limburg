"""
Authorised listing feed adapter skeleton.

Expected normalized property:
{
  "source_id": "...",
  "city": "...",
  "street": "...",
  "price": 250000,
  "size_m2": 120,
  "plot_m2": 200,
  "energy_label": "D",
  "property_type": "Tussenwoning",
  "build_year": 1975,
  "source_url": "https://..."
}
"""

def fetch_listings(api_key: str):
    # Add official feed/API call here.
    # Never scrape a source if its terms disallow it.
    return []
