import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")

DB_PATH = os.getenv("DB_PATH", "medrobo.db")

PIPELINE_SCHEDULE_HOUR = int(os.getenv("PIPELINE_SCHEDULE_HOUR", "7"))
PIPELINE_SCHEDULE_MINUTE = int(os.getenv("PIPELINE_SCHEDULE_MINUTE", "0"))
# A failed scheduled run is retried once after this delay, then an alert email is sent
PIPELINE_RETRY_DELAY_MINUTES = int(os.getenv("PIPELINE_RETRY_DELAY_MINUTES", "30"))

# combined: one email with a section per industry; per_industry: one email each
DIGEST_MODE = os.getenv("DIGEST_MODE", "combined")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# Primary data sources. SEC requires a descriptive User-Agent with a contact email.
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "")
OPENFDA_API_KEY = os.getenv("OPENFDA_API_KEY", "")
NCBI_API_KEY = os.getenv("NCBI_API_KEY", "")

# Tavily recency filter: day | week | month | year
SEARCH_TIME_RANGE = os.getenv("SEARCH_TIME_RANGE", "week")
EXTRACTION_BATCH_SIZE = int(os.getenv("EXTRACTION_BATCH_SIZE", "15"))
