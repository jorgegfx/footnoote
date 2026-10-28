"""PubMed via NCBI E-utilities (esearch for ids, efetch for records). Biomedical questions only."""

import xml.etree.ElementTree as ET
from datetime import date

from footnote.identifiers import normalize_doi
from footnote.models import Family, QueryPlan, SourceResult
from footnote.sources.base import NativeAdapter, register

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ESEARCH_URL = f"{BASE_URL}/esearch.fcgi"
EFETCH_URL = f"{BASE_URL}/efetch.fcgi"
RETMAX = 8
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1
)}  # fmt: skip


@register("pubmed")
class PubMedAdapter(NativeAdapter):
    family = Family.ACADEMIC

    def accepts(self, plan: QueryPlan) -> bool:
        return plan.biomedical and super().accepts(plan)

    async def search(self, plan: QueryPlan) -> list[SourceResult]:
        queries = plan.queries.get(Family.ACADEMIC, [])
        # One esearch for all rewrites keeps us inside NCBI's 3 requests/second without a key.
        term = " OR ".join(f"({q})" for q in queries)
        params: dict[str, str | int] = {
            **self._common_params(),
            "db": "pubmed",
            "term": term,
            "retmode": "json",
            "retmax": RETMAX,
            "sort": "relevance",
        }
        if plan.time_window:
            params |= {
                "datetype": "pdat",
                "mindate": plan.time_window[0].strftime("%Y/%m/%d"),
                "maxdate": plan.time_window[1].strftime("%Y/%m/%d"),
            }
        response = await self.client.get(ESEARCH_URL, params=params)
        response.raise_for_status()
        ids: list[str] = response.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []

        response = await self.client.get(
            EFETCH_URL,
            params={**self._common_params(), "db": "pubmed", "id": ",".join(ids), "retmode": "xml"},
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
        results = [self._to_result(a) for a in root.findall("PubmedArticle")]
        return [r for r in results if r is not None]

    def _common_params(self) -> dict[str, str]:
        params = {"tool": "footnote"}
        if self.settings.contact_email:
            params["email"] = self.settings.contact_email
        if self.settings.ncbi_api_key:
            params["api_key"] = self.settings.ncbi_api_key.get_secret_value()
        return params

    def _to_result(self, record: ET.Element) -> SourceResult | None:
        pmid = _text(record.find("MedlineCitation/PMID"))
        article = record.find("MedlineCitation/Article")
        if not pmid or article is None:
            return None
        title = _text(article.find("ArticleTitle"))
        if not title:
            return None
        doi = next(
            (
                normalize_doi(i.text)
                for i in record.findall("PubmedData/ArticleIdList/ArticleId")
                if i.get("IdType") == "doi"
            ),
            None,
        )
        types = {_text(t).lower() for t in article.findall("PublicationTypeList/PublicationType")}
        return SourceResult(
            source_id=self.id,
            family=Family.ACADEMIC,
            title=title,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            snippet=_abstract(article),
            published_at=_published(article),
            authors=_authors(article),
            venue=_text(article.find("Journal/Title")) or None,
            doi=doi,
            pmid=pmid,
            is_preprint="preprint" in types,
            trust=self.trust,
        )


def _text(element: ET.Element | None) -> str:
    """All text inside an element, including inline markup like <i>."""
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def _abstract(article: ET.Element) -> str:
    parts = []
    for section in article.findall("Abstract/AbstractText"):
        text = _text(section)
        label = section.get("Label")
        parts.append(f"{label.capitalize()}: {text}" if label and text else text)
    return " ".join(p for p in parts if p)


def _authors(article: ET.Element) -> list[str]:
    names = []
    for author in article.findall("AuthorList/Author"):
        collective = _text(author.find("CollectiveName"))
        name = " ".join(
            p for p in (_text(author.find("ForeName")), _text(author.find("LastName"))) if p
        )
        if name or collective:
            names.append(name or collective)
    return names


def _published(article: ET.Element) -> date | None:
    """Electronic publication date when present, else the journal issue date."""
    for path in ("ArticleDate", "Journal/JournalIssue/PubDate"):
        element = article.find(path)
        if element is not None and (parsed := _date_parts(element)):
            return parsed
    medline = _text(article.find("Journal/JournalIssue/PubDate/MedlineDate"))
    return _safe_date(int(medline[:4]), 1, 1) if medline[:4].isdigit() else None


def _date_parts(element: ET.Element) -> date | None:
    year = _text(element.find("Year"))
    if not year.isdigit():
        return None
    month_text = _text(element.find("Month")).lower()
    month = int(month_text) if month_text.isdigit() else MONTHS.get(month_text[:3], 1)
    day_text = _text(element.find("Day"))
    return _safe_date(int(year), month, int(day_text) if day_text.isdigit() else 1)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None
