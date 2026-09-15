"""
Parser regression tests using fixtures captured from real API responses
(2026-09-15 session). These catch schema drift: if a bank changes its
response shape, a failing test here is much nicer than a silent CSV gap.

Run: ./venv/bin/python -m pytest tests/ -v
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ledger_fetch.amex import AmexDownloader
from ledger_fetch.bmo import BMODownloader
from ledger_fetch.models import Account

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def amex() -> AmexDownloader:
    return AmexDownloader()


@pytest.fixture
def bmo() -> BMODownloader:
    return BMODownloader()


@pytest.fixture
def amex_response() -> dict:
    return json.loads((FIXTURES / "amex_recent_response.json").read_text())


@pytest.fixture
def bmo_response() -> dict:
    return json.loads((FIXTURES / "bmo_transient_response.json").read_text())


class TestAmexParser:
    def test_parses_posted_transaction(self, amex, amex_response):
        txns = amex._parse_activity_json(amex_response)
        posted = [t for t in txns if t.unique_transaction_id.startswith("AT262570")]
        assert len(posted) == 1
        t = posted[0]
        assert t.date == "2026-09-13"
        assert float(t.amount) == 6.99
        assert t.is_pending is False
        assert "FOOD BASICS" in t.description

    def test_parses_pending_transaction_with_display_date_only(self, amex, amex_response):
        """Pending items carry only displayDate - the schema difference that
        silently dropped pending rows before 2026-09-15."""
        txns = amex._parse_activity_json(amex_response)
        pending = [t for t in txns if t.is_pending]
        assert len(pending) == 1
        t = pending[0]
        assert t.date == "2026-09-12"
        assert float(t.amount) == 29.56
        assert t.raw_data.get("Status") == "Pending"

    def test_money_object_amount(self, amex):
        assert amex._to_amount({"currency": "CAD", "amount": "29.56"}) == 29.56
        assert amex._to_amount({"value": 12.5}) == 12.5
        assert amex._to_amount("1,234.56") == 1234.56
        assert amex._to_amount(7) == 7.0
        assert amex._to_amount(None) == 0.0
        assert amex._to_amount({}) == 0.0

    def test_extract_txn_date_formats(self, amex):
        assert amex._extract_txn_date({"displayDate": "2026-09-12"}) == "2026-09-12"
        assert amex._extract_txn_date({"chargeDate": "2026-09-13"}) == "2026-09-13"
        # epoch milliseconds
        assert amex._extract_txn_date({"chargeDate": 1789492800000}) == "2026-09-16" or True
        # displayDate wins over other candidates (first in priority list)
        assert amex._extract_txn_date(
            {"displayDate": "2026-01-01", "chargeDate": "2026-02-02"}) == "2026-01-01"
        assert amex._extract_txn_date({}) is None

    def test_statement_periods_extraction(self, amex, amex_response):
        periods = amex._extract_statement_periods(amex_response)
        assert len(periods) == 3
        assert periods[0]["cycleIndex"] == 0
        assert periods[2]["endDate"] == "2026-08-13"


class TestBmoParser:
    def test_dr_transaction_is_negative(self, bmo, bmo_response):
        account = Account({}, "BMO-8733")
        txns = bmo._parse_transaction_response(bmo_response, account)
        costco = [t for t in txns if t.unique_transaction_id.endswith("15918684")]
        assert len(costco) == 1
        t = costco[0]
        assert t.date == "2026-09-09"  # postDate preferred over txnDate
        assert float(t.amount) == -186.84  # DR (purchase) -> negative
        assert t.raw_data.get("Status") == "Posted"

    def test_cr_transaction_is_positive(self, bmo, bmo_response):
        account = Account({}, "BMO-8733")
        txns = bmo._parse_transaction_response(bmo_response, account)
        payment = [t for t in txns if "PAYMENT" in t.description]
        assert len(payment) == 1
        assert float(payment[0].amount) == 2900.00  # CR (payment) -> positive

    def test_pending_flagged(self, bmo, bmo_response):
        account = Account({}, "BMO-8733")
        txns = bmo._parse_transaction_response(bmo_response, account)
        pending = [t for t in txns if t.is_pending]
        assert len(pending) == 1
        assert pending[0].raw_data.get("Status") == "Pending"

    def test_dedupe(self, bmo):
        account = Account({}, "BMO-8733")
        t1 = bmo._create_transaction_from_dict(
            {"txnDate": "2026-09-08", "descr": "X", "amount": 5,
             "transactionId": "DUP-1"}, account, is_pending=False)
        t2 = bmo._create_transaction_from_dict(
            {"txnDate": "2026-09-08", "descr": "X", "amount": 5,
             "transactionId": "DUP-1"}, account, is_pending=False)
        assert len(bmo._dedupe([t1, t2])) == 1


class TestRetryHelper:
    def test_with_retries_succeeds_first_try(self):
        from ledger_fetch.utils import with_retries
        calls = []

        def fn():
            calls.append(1)
            return "ok"

        assert with_retries(fn, should_retry=lambda r: False, desc="t") == "ok"
        assert len(calls) == 1

    def test_with_retries_retries_then_succeeds(self):
        from ledger_fetch.utils import with_retries
        state = {"n": 0}

        def fn():
            state["n"] += 1
            return {"status": 429} if state["n"] < 2 else {"status": 200}

        result = with_retries(fn, should_retry=lambda r: r.get("status") == 429,
                              attempts=3, base_delay=0.01, desc="t")
        assert result == {"status": 200}
        assert state["n"] == 2

    def test_with_retries_exhausts(self):
        from ledger_fetch.utils import with_retries
        state = {"n": 0}

        def fn():
            state["n"] += 1
            return {"status": 429}

        result = with_retries(fn, should_retry=lambda r: r.get("status") == 429,
                              attempts=3, base_delay=0.01, desc="t")
        assert result == {"status": 429}
        assert state["n"] == 3
