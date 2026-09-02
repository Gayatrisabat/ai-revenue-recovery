from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from domain.enums import CaseState, Currency, RiskTier
from domain.models import RecoveryCase
from ingestion.service import IngestionService
from ingestion.signature import LocalTestSignatureVerifier
from ingestion.simulator import LocalEventSimulator
from ingestion.stores import IngestionDatabase

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "ingestion" / "fixtures"


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 23, 10, 30, 0, tzinfo=timezone.utc)


def make_case(state: CaseState, *, now: datetime, **overrides) -> RecoveryCase:
    defaults = dict(
        case_id="case_001",
        customer_id="cus_123",
        subscription_id="sub_456",
        principal_amount_minor=149900,
        currency=Currency.INR,
        state=state,
        created_at=now,
        updated_at=now,
        risk_tier=RiskTier.STANDARD,
    )
    defaults.update(overrides)
    return RecoveryCase(**defaults)


@pytest.fixture
def case_factory(now):
    def _factory(state: CaseState, **overrides) -> RecoveryCase:
        return make_case(state, now=now, **overrides)

    return _factory


# ---------------------------------------------------------------------------
# Ingestion-layer fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def verifier() -> LocalTestSignatureVerifier:
    """Shared-secret verifier used by both the service and the simulator
    in tests, so simulator-signed fixtures verify against the service."""
    return LocalTestSignatureVerifier(secret="test-fixture-secret")


@pytest.fixture
def db() -> IngestionDatabase:
    return IngestionDatabase()


@pytest.fixture
def service(db, verifier) -> IngestionService:
    return IngestionService(db, verifier)


@pytest.fixture
def simulator(verifier) -> LocalEventSimulator:
    return LocalEventSimulator(signer=verifier)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


# ---------------------------------------------------------------------------
# Policy-engine fixtures
# ---------------------------------------------------------------------------

POLICIES_DIR = Path(__file__).resolve().parent.parent / "policies"


@pytest.fixture
def policy_config():
    from policy.config import load_policy_config

    return load_policy_config(POLICIES_DIR / "recovery-policy.yaml")


@pytest.fixture
def economic_tables():
    from policy.config import load_economic_tables

    return load_economic_tables(POLICIES_DIR / "economic-tables.yaml")


@pytest.fixture
def policy_engine(policy_config, economic_tables, db):
    from policy.engine import RecoveryPolicyEngine

    return RecoveryPolicyEngine(policy_config, economic_tables, db.cases, db.audit_log)
