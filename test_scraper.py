import asyncio
import logging
import json
from app.scrapers.acis_scraper import scrape_case_data

logging.basicConfig(level=logging.INFO)

async def test():
    try:
        # Using the A-number from user's logs
        result = await scrape_case_data("246301729", "INDIA")
        print("\nSCRAPE RESULT:")
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"FAILED: {e}")

if __name__ == "__main__":
    asyncio.run(test())
