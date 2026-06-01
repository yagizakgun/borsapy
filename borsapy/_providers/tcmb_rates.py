"""TCMB interest rates provider.

Fetches central bank policy rates from tcmb.gov.tr:
- 1-week repo rate (policy rate)
- Overnight (O/N) corridor rates
- Late liquidity window (LON) rates
"""

from datetime import datetime

from bs4 import BeautifulSoup

from borsapy._providers.base import BaseProvider
from borsapy.cache import TTL

# TCMB interest rate page URLs
TCMB_URLS = {
    "policy": "https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/Temel+Faaliyetler/Para+Politikasi/Merkez+Bankasi+Faiz+Oranlari/1+Hafta+Repo",
    "overnight": "https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/Temel+Faaliyetler/Para+Politikasi/Merkez+Bankasi+Faiz+Oranlari/faiz-oranlari",
    "late_liquidity": "https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/Temel+Faaliyetler/Para+Politikasi/Merkez+Bankasi+Faiz+Oranlari/Gec+Likidite+Penceresi+%28LON%29",
}


class TCMBRatesProvider(BaseProvider):
    """Provider for TCMB interest rates."""

    def _parse_turkish_number(self, text: str) -> float | None:
        """Parse Turkish number format (comma as decimal separator).

        Examples:
            "38,00" -> 38.0
            "36,50" -> 36.5
            "-" -> None
        """
        text = text.strip()
        if not text or text == "-":
            return None
        try:
            # Turkish format: comma is decimal separator
            return float(text.replace(",", "."))
        except ValueError:
            return None

    def _parse_date(self, text: str) -> datetime | None:
        """Parse TCMB date format.

        Examples:
            "12.12.25" -> datetime(2025, 12, 12)
            "01.02.2024" -> datetime(2024, 2, 1)
        """
        text = text.strip()
        if not text:
            return None

        try:
            # Try short format first (DD.MM.YY)
            if len(text.split(".")[-1]) == 2:
                return datetime.strptime(text, "%d.%m.%y")
            # Try full format (DD.MM.YYYY)
            return datetime.strptime(text, "%d.%m.%Y")
        except ValueError:
            return None

    def _fetch_and_parse_table(self, url: str) -> list[dict]:
        """Fetch URL and parse HTML table.

        Returns list of dicts with date, borrowing, lending columns.
        """
        cache_key = f"tcmb_rates:{url}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        response = self._get(url)
        html = response.text
        soup = BeautifulSoup(html, "lxml")

        # Find the main data table
        table = soup.find("table")
        if not table:
            return []

        rows = table.find_all("tr")
        if not rows:
            return []

        results = []
        for row in rows[1:]:  # Skip header row
            cols = row.find_all("td")
            if len(cols) < 3:
                continue

            date = self._parse_date(cols[0].text)
            borrowing = self._parse_turkish_number(cols[1].text)
            lending = self._parse_turkish_number(cols[2].text)

            if date:
                results.append({
                    "date": date,
                    "borrowing": borrowing,
                    "lending": lending,
                })

        # Cache for 5 minutes
        self._cache_set(cache_key, results, TTL.FX_RATES)
        return results

    def get_policy_rate(self) -> dict:
        """Get current 1-week repo rate (policy rate).

        Returns:
            Dict with date and lending rate (policy rate).

        Example:
            {"date": datetime(2025, 12, 12), "lending": 38.0}
        """
        data = self._fetch_and_parse_table(TCMB_URLS["policy"])
        if not data:
            return {"date": None, "lending": None}

        # En güncel oran son satırdır (tablo en eskiden en yeniye sıralı)
        latest = max(data, key=lambda r: r["date"])
        return {
            "date": latest["date"],
            "lending": latest["lending"],
        }

    def get_overnight_rates(self) -> dict:
        """Get overnight (O/N) corridor rates.

        Returns:
            Dict with date, borrowing, and lending rates.

        Example:
            {"date": datetime(2025, 12, 12), "borrowing": 36.5, "lending": 41.0}
        """
        data = self._fetch_and_parse_table(TCMB_URLS["overnight"])
        if not data:
            return {"date": None, "borrowing": None, "lending": None}

        latest = max(data, key=lambda r: r["date"])
        return {
            "date": latest["date"],
            "borrowing": latest["borrowing"],
            "lending": latest["lending"],
        }

    def get_late_liquidity_rates(self) -> dict:
        """Get late liquidity window (LON) rates.

        Returns:
            Dict with date, borrowing, and lending rates.

        Example:
            {"date": datetime(2025, 12, 12), "borrowing": 0.0, "lending": 44.0}
        """
        data = self._fetch_and_parse_table(TCMB_URLS["late_liquidity"])
        if not data:
            return {"date": None, "borrowing": None, "lending": None}

        latest = max(data, key=lambda r: r["date"])
        return {
            "date": latest["date"],
            "borrowing": latest["borrowing"],
            "lending": latest["lending"],
        }

    def get_all_rates(self) -> list[dict]:
        """Get all current TCMB interest rates.

        Returns:
            List of dicts with rate_type, borrowing, and lending.
        """
        policy = self.get_policy_rate()
        overnight = self.get_overnight_rates()
        late_liquidity = self.get_late_liquidity_rates()

        return [
            {
                "rate_type": "policy",
                "date": policy["date"],
                "borrowing": None,
                "lending": policy["lending"],
            },
            {
                "rate_type": "overnight",
                "date": overnight["date"],
                "borrowing": overnight["borrowing"],
                "lending": overnight["lending"],
            },
            {
                "rate_type": "late_liquidity",
                "date": late_liquidity["date"],
                "borrowing": late_liquidity["borrowing"],
                "lending": late_liquidity["lending"],
            },
        ]

    def get_rate_history(self, rate_type: str = "policy") -> list[dict]:
        """Get historical rates for given type.

        Args:
            rate_type: One of "policy", "overnight", "late_liquidity"

        Returns:
            List of dicts with date, borrowing, lending values.
            Sorted by date ascending (oldest first).
        """
        if rate_type not in TCMB_URLS:
            raise ValueError(f"Invalid rate_type: {rate_type}. Must be one of {list(TCMB_URLS.keys())}")

        return self._fetch_and_parse_table(TCMB_URLS[rate_type])


# Singleton instance
_provider: TCMBRatesProvider | None = None


def get_tcmb_rates_provider() -> TCMBRatesProvider:
    """Get the singleton TCMB rates provider instance."""
    global _provider
    if _provider is None:
        _provider = TCMBRatesProvider()
    return _provider
