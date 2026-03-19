import re
import asyncio
import logging

from app.scrapers.acis_scraper import scrape_case_data

logger = logging.getLogger(__name__)

# One scrape at a time because you are using a shared profile
SCRAPE_LOCK = asyncio.Lock()


def normalize_anumber(a_number: str) -> str:
    digits = re.sub(r"\D", "", a_number or "")
    if len(digits) != 9:
        raise ValueError("A-number must contain exactly 9 digits.")
    return digits


async def fetch_acis_case(a_number: str, nationality: str = "INDIA") -> dict:
    normalized_a_number = normalize_anumber(a_number)

    logger.info("Received request for A-number: %s", normalized_a_number)

    async with SCRAPE_LOCK:
        logger.info("Acquired scrape lock for A-number: %s", normalized_a_number)
        result = await scrape_case_data(
            a_number=normalized_a_number,
            nationality=nationality
        )
        logger.info("Released scrape lock for A-number: %s", normalized_a_number)
        return result