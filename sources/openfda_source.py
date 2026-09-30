import logging
from datetime import date

from config import OPENFDA_API_KEY
from pipeline.models import RawResult
from profiles.loader import IndustryProfile
from sources import http

logger = logging.getLogger(__name__)

BASE_URL = "https://api.fda.gov/device/510k.json"
PMN_URL = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/pmn.cfm?ID={id}"
DENOVO_URL = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/denovo.cfm?ID={id}"


def _term(field: str, value: str) -> str:
    value = value.strip()
    if any(c in value for c in ' -()/'):
        value = '"' + value.replace('"', "") + '"'
    return f"{field}:{value}"


def build_search(keywords: list[str], product_codes: list[str], since: date, until: date) -> str:
    """openFDA search string; '+' is how openFDA encodes spaces between clauses."""
    date_clause = f"decision_date:[{since:%Y%m%d}+TO+{until:%Y%m%d}]"
    terms = [_term("device_name", k) for k in keywords] + [_term("product_code", c) for c in product_codes]
    if not terms:
        return date_clause
    return f"{date_clause}+AND+(" + "+OR+".join(t.replace(" ", "+") for t in terms) + ")"


def parse_results(data: dict, profile: IndustryProfile) -> list[RawResult]:
    results: list[RawResult] = []
    for r in data.get("results", []):
        k_number = r.get("k_number") or ""
        if not k_number:
            continue
        is_de_novo = k_number.upper().startswith("DEN")
        applicant = (r.get("applicant") or "").strip()
        device = (r.get("device_name") or "").strip()
        decision_date = r.get("decision_date")
        status = "FDA De Novo Granted" if is_de_novo else "FDA 510(k) Cleared"
        url = (DENOVO_URL if is_de_novo else PMN_URL).format(id=k_number)
        facts = {
            "company": applicant or None,
            "product": device or None,
            "event_type": profile.coerce_event_type("regulatory"),
            "date": decision_date,
            "regulatory_status": status,
        }
        snippet = (
            f"{status}: {k_number}. Applicant: {applicant}. Device: {device}. "
            f"Decision date: {decision_date}. Decision: {r.get('decision_description')}. "
            f"Product code: {r.get('product_code')}. Clearance type: {r.get('clearance_type')}. "
            f"Advisory committee: {r.get('advisory_committee_description')}."
        )
        results.append(
            RawResult(
                title=f"{status}: {device} ({applicant})",
                url=url,
                snippet=snippet,
                source="accessdata.fda.gov",
                date=decision_date,
                query_tag=f"{facts['event_type']}:other",
                source_type=OpenFDA510kSource.source_type,
                prefilled=facts,
            )
        )
    return results


class OpenFDA510kSource:
    name = "openfda_510k"
    source_type = "openfda"
    description = "Official FDA 510(k) clearances and De Novo grants for medical devices (US)."
    config_schema = {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "array", "items": {"type": "string"},
                "description": "Words matched against the FDA device name, e.g. robotic",
            },
            "product_codes": {
                "type": "array", "items": {"type": "string"},
                "description": "Optional 3-letter FDA product codes",
            },
        },
    }
    default_lookback_days = 45  # FDA posts decisions with a lag of a few weeks

    def fetch(self, profile: IndustryProfile, since: date) -> list[RawResult]:
        cfg = profile.sources.get(self.name, {})
        search = build_search(cfg.get("keywords", []), cfg.get("product_codes", []), since, date.today())
        limit = min(int(cfg.get("max_results", 50)), 100)
        url = f"{BASE_URL}?search={search}&limit={limit}&sort=decision_date:desc"
        if OPENFDA_API_KEY:
            url += f"&api_key={OPENFDA_API_KEY}"
        try:
            response = http.get(url)
        except Exception as e:
            # openFDA answers 404 when a search has no matches
            if getattr(getattr(e, "response", None), "status_code", None) == 404:
                return []
            raise
        return parse_results(response.json(), profile)
