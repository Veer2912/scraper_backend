import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

ACIS_URL = os.getenv("ACIS_URL", "https://acis.eoir.justice.gov/en/")
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "change-this-secret")

# On Windows RDP server, this is a good default:
DEFAULT_PROFILE_PATH = os.path.join(
    os.environ.get("TEMP", str(BASE_DIR)),
    "nodriver_profile"
)

NODRIVER_PROFILE_PATH = os.getenv("NODRIVER_PROFILE_PATH", DEFAULT_PROFILE_PATH)
SCRAPER_HEADLESS = os.getenv("SCRAPER_HEADLESS", "true").lower() == "true"
KEEP_BROWSER_OPEN_SECONDS = int(os.getenv("KEEP_BROWSER_OPEN_SECONDS", "0"))

# Keep the same UA because that is what is working on your Windows remote desktop
SCRAPER_USER_AGENT = os.getenv(
    "SCRAPER_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "180"))
RESULT_WAIT_ATTEMPTS = int(os.getenv("RESULT_WAIT_ATTEMPTS", "20"))
RESULT_WAIT_SLEEP_SECONDS = float(os.getenv("RESULT_WAIT_SLEEP_SECONDS", "2"))
CLOUDFLARE_WAIT_ATTEMPTS = int(os.getenv("CLOUDFLARE_WAIT_ATTEMPTS", "15"))
CLOUDFLARE_WAIT_SLEEP_SECONDS = float(os.getenv("CLOUDFLARE_WAIT_SLEEP_SECONDS", "3"))