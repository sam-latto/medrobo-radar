import logging
import xml.etree.ElementTree as ET
from datetime import date

from config import SEC_USER_AGENT
from pipeline.models import RawResult
from profiles.loader import IndustryProfile
from sources import http

logger = logging.getLogger(__name__)

SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{adsh_nodash}/primary_doc.xml"
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{adsh_nodash}/{adsh}-index.htm"

DEFAULT_EXCLUDED_GROUPS = ["Pooled Investment Fund"]


def _text(root: ET.Element, path: str) -> str | None:
    node = root.find(path)
    return node.text.strip() if node is not None and node.text and node.text.strip() else None


def _int(value: str | None) -> int | None:
    try:
        return int(float(value)) if value else None
    except ValueError:
        return None  # e.g. "Indefinite"


def parse_search_hits(data: dict) -> list[dict]:
    """EDGAR full-text search response -> [{adsh, cik, name, file_date}] for original Form Ds."""
    filings = []
    for hit in data.get("hits", {}).get("hits", []):
        src = hit.get("_source", {})
        if src.get("file_type") != "D":  # skip D/A amendments
            continue
        ciks = src.get("ciks") or []
        adsh = src.get("adsh")
        if not ciks or not adsh:
            continue
        names = src.get("display_names") or [""]
        filings.append({
            "adsh": adsh,
            "cik": str(int(ciks[0])),
            "name": names[0].split("  (CIK")[0].strip(),
            "file_date": src.get("file_date"),
        })
    return filings


def parse_primary_doc(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)
    offering = "offeringData/"
    return {
        "entity_name": _text(root, "primaryIssuer/entityName"),
        "city": _text(root, "primaryIssuer/issuerAddress/city"),
        "state": _text(root, "primaryIssuer/issuerAddress/stateOrCountryDescription"),
        "year_of_inc": _text(root, "primaryIssuer/yearOfInc/value"),
        "industry_group": _text(root, offering + "industryGroup/industryGroupType"),
        "is_amendment": _text(root, offering + "typeOfFiling/newOrAmendment/isAmendment") == "true",
        "date_of_first_sale": _text(root, offering + "typeOfFiling/dateOfFirstSale/value"),
        "securities": _text(root, offering + "typesOfSecuritiesOffered/descriptionOfOtherType")
        or ("Equity" if _text(root, offering + "typesOfSecuritiesOffered/isEquityType") == "true" else None),
        "total_offering_amount": _int(_text(root, offering + "offeringSalesAmounts/totalOfferingAmount")),
        "total_amount_sold": _int(_text(root, offering + "offeringSalesAmounts/totalAmountSold")),
        "num_investors": _int(_text(root, offering + "investors/totalNumberAlreadyInvested")),
    }


def filing_to_result(filing: dict, doc: dict, profile: IndustryProfile) -> RawResult:
    name = doc.get("entity_name") or filing["name"]
    sold = doc.get("total_amount_sold")
    offered = doc.get("total_offering_amount")
    location = ", ".join(p for p in [doc.get("city"), doc.get("state")] if p)
    event_type = profile.coerce_event_type("funding")

    amount_text = f"${sold:,.0f} sold" if sold is not None else "amount sold not disclosed"
    if offered:
        amount_text += f" of a ${offered:,.0f} offering"
    snippet = (
        f"SEC Form D filed {filing.get('file_date')} by {name}"
        f"{f' ({location})' if location else ''}. Industry group: {doc.get('industry_group')}. "
        f"{amount_text}. Securities: {doc.get('securities') or 'n/a'}. "
        f"Date of first sale: {doc.get('date_of_first_sale') or 'n/a'}. "
        f"Investors: {doc.get('num_investors') if doc.get('num_investors') is not None else 'n/a'}. "
        f"Incorporated: {doc.get('year_of_inc') or 'n/a'}."
    )
    facts = {
        "company": name,
        "event_type": event_type,
        "date": filing.get("file_date"),
        "funding_amount": sold or None,
    }
    adsh = filing["adsh"]
    return RawResult(
        title=f"SEC Form D: {name} — {amount_text}",
        url=INDEX_URL.format(cik=filing["cik"], adsh_nodash=adsh.replace("-", ""), adsh=adsh),
        snippet=snippet,
        source="sec.gov",
        date=filing.get("file_date"),
        query_tag=f"{event_type}:other",
        source_type=SecFormDSource.source_type,
        prefilled=facts,
    )


class SecFormDSource:
    name = "sec_form_d"
    source_type = "sec_form_d"
    description = (
        "SEC EDGAR Form D filings: private fundraises with issuer and amount sold (US). "
        "Form D has no business description, so keywords match mostly issuer names."
    )
    config_schema = {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "array", "items": {"type": "string"},
                "description": "Short terms likely to appear in issuer names, e.g. robotics",
            },
            "industry_groups": {
                "type": "array", "items": {"type": "string"},
                "description": "Optional Form D industry groups to keep, e.g. 'Other Health Care'",
            },
        },
    }
    default_lookback_days = 14

    def fetch(self, profile: IndustryProfile, since: date) -> list[RawResult]:
        if not SEC_USER_AGENT:
            logger.warning("SEC_USER_AGENT is not set (SEC requires a contact email); skipping sec_form_d")
            return []
        cfg = profile.sources.get(self.name, {})
        headers = {"User-Agent": SEC_USER_AGENT}
        keywords = list(cfg.get("keywords", []))
        if cfg.get("include_watchlist", False):
            keywords += profile.watchlist
        keep_groups = set(cfg.get("industry_groups", []))
        exclude_groups = set(cfg.get("exclude_industry_groups", DEFAULT_EXCLUDED_GROUPS))
        max_filings = int(cfg.get("max_filings", 25))

        filings: dict[str, dict] = {}
        for keyword in keywords:
            q = f'"{keyword}"' if " " in keyword else keyword
            params = {
                "q": q,
                "forms": "D",
                "dateRange": "custom",
                "startdt": since.isoformat(),
                "enddt": date.today().isoformat(),
            }
            try:
                data = http.get(SEARCH_URL, params=params, headers=headers).json()
            except Exception as e:
                logger.error(f"EDGAR search failed for '{keyword}': {e}")
                continue
            for f in parse_search_hits(data):
                filings.setdefault(f["adsh"], f)

        results: list[RawResult] = []
        for filing in list(filings.values())[:max_filings]:
            doc_url = DOC_URL.format(cik=filing["cik"], adsh_nodash=filing["adsh"].replace("-", ""))
            try:
                doc = parse_primary_doc(http.get(doc_url, headers=headers).text)
            except Exception as e:
                logger.error(f"Could not read Form D {filing['adsh']}: {e}")
                continue
            group = doc.get("industry_group")
            if doc.get("is_amendment") or group in exclude_groups:
                continue
            if keep_groups and group not in keep_groups:
                continue
            results.append(filing_to_result(filing, doc, profile))
        return results
