# -*- coding: utf-8 -*-
"""The frozen synthetic schema-18 installation (step P0a): intact, complete, synthetic, reproducible."""

from __future__ import annotations

import gzip
import shutil
import sqlite3
from pathlib import Path

import pytest

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.database import billing
from shareomat.database import sqlite as sqlite_module
from shareomat.database.sqlite import init_db
from shareomat.web.state import get_state
from tests.synthetic.schema18 import build_schema18, content_fingerprint

FIXTURE = Path(__file__).parent / "fixtures" / "schema18_synthetic"


@pytest.fixture(scope="module")
def frozen(tmp_path_factory) -> Path:
    target = tmp_path_factory.mktemp("schema18") / "shareomat.db"
    with gzip.open(FIXTURE / "shareomat-schema18.db.gz", "rb") as src, open(target, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return target


def _one(db: Path, sql: str):
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def test_fixture_is_an_intact_schema_18_database(frozen):
    assert _one(frozen, "PRAGMA integrity_check") == "ok"
    assert _one(frozen, "SELECT COUNT(*) FROM pragma_foreign_key_check") == 0
    assert _one(frozen, "SELECT MAX(version) FROM schema_migrations") == 18
    assert content_fingerprint(frozen) == (FIXTURE / "fingerprint.txt").read_text(encoding="utf-8").strip()


# Each state of chapter 16.1 with the query that proves it is in the fixture.
STATES = {
    "Bezüger, Produzent, Prosumer, Speicher": "SELECT COUNT(DISTINCT role) = 4 FROM meters",
    "Teilnehmer mit zwei Messpunkten": "SELECT MAX(n) >= 2 FROM (SELECT COUNT(*) n FROM meters GROUP BY participant_id)",
    "inaktiver Teilnehmer und Messpunkt": "SELECT (SELECT COUNT(*) FROM participants WHERE active = 0) > 0"
                                          " AND (SELECT COUNT(*) FROM meters WHERE active = 0) > 0",
    "Messpunkt mit Ende der Gültigkeit": "SELECT COUNT(*) > 0 FROM meters WHERE valid_until IS NOT NULL",
    "Anlagenleistung": "SELECT COUNT(*) > 0 FROM meters WHERE generation_kwp IS NOT NULL",
    "Teilnehmer ohne Messpunkt": "SELECT COUNT(*) > 0 FROM participants p WHERE NOT EXISTS"
                                 " (SELECT 1 FROM meters m WHERE m.participant_id = p.id)",
    "Austritt mit Grund": "SELECT COUNT(*) > 0 FROM memberships WHERE left_at IS NOT NULL AND exit_reason != ''",
    "Wiedereintritt": "SELECT MAX(n) >= 2 FROM (SELECT COUNT(*) n FROM memberships GROUP BY participant_id)",
    "gemeldet, nicht bestätigt": "SELECT COUNT(*) > 0 FROM memberships WHERE vnb_notified_at IS NOT NULL"
                                 " AND vnb_confirmed_at IS NULL",
    "wirksamer Beginn nach Beitritt": "SELECT COUNT(*) > 0 FROM memberships WHERE effective_start_at > joined_at",
    "offener Prüfhinweis": "SELECT COUNT(*) > 0 FROM memberships WHERE review_note != ''",
    "bestätigter Prüfhinweis": "SELECT COUNT(*) > 0 FROM audit_events WHERE action = 'membership_review_confirmed'",
    "Vertragsstatus veröffentlicht, zurückgezogen, Entwurf": "SELECT COUNT(DISTINCT status) = 3 FROM contract_versions",
    "Version «mit Zustimmung aller»": "SELECT COUNT(*) > 0 FROM contract_versions WHERE consent_based = 1",
    "befristete Version": "SELECT COUNT(*) > 0 FROM contract_versions WHERE status = 'published' AND valid_until IS NOT NULL",
    "beendeter Vertrag": "SELECT COUNT(*) > 0 FROM audit_events WHERE entity = 'contract_version' AND action = 'ended'",
    "Erklärung ohne Unterschrift": "SELECT COUNT(*) > 0 FROM participant_contract WHERE declaration_issued_at IS NOT NULL"
                                   " AND accepted_at IS NULL AND notified_at IS NULL",
    "Unterschrift mit Dokument": "SELECT COUNT(*) > 0 FROM participant_contract WHERE signed_document_location != ''",
    "Unterschrift auf Papier": "SELECT COUNT(*) > 0 FROM participant_contract WHERE paper_original_location != ''",
    "Unterschrift entfernt": "SELECT COUNT(*) > 0 FROM audit_events WHERE action = 'signature_removed'",
    "Mitteilung per E-Mail, Brief, von Hand": "SELECT COUNT(DISTINCT notice_method) >= 3 FROM participant_contract"
                                              " WHERE notice_method != ''",
    "Mitteilung zurückgenommen": "SELECT COUNT(*) > 0 FROM audit_events WHERE action = 'corrected' AND detail LIKE '%Mitteilung%'",
    "Zustimmung erfasst": "SELECT COUNT(*) > 0 FROM participant_contract WHERE consent_at IS NOT NULL",
    "Versandprotokoll gesendet, fehlgeschlagen, unklar, Brief": "SELECT COUNT(DISTINCT status) >= 4 FROM contract_notices",
    "Tarif ohne Vertrag (B3)": "SELECT COUNT(*) > 0 FROM tariffs t WHERE NOT EXISTS"
                               " (SELECT 1 FROM contract_versions v WHERE v.tariff_id = t.id)",
    "SDAT E66 und E31": "SELECT COUNT(DISTINCT document_type) = 2 FROM sdat_documents WHERE outcome = 'imported'",
    "SDAT abgewiesen": "SELECT COUNT(DISTINCT reason_code) >= 3 FROM sdat_documents WHERE outcome != 'imported'",
    "SDAT fremde Messreihe gezählt": "SELECT SUM(foreign_series_count) > 0 FROM sdat_documents",
    "SDAT vorläufige Werte (21)": "SELECT COUNT(*) > 0 FROM sdat_series WHERE observations LIKE '%;21%'",
    "SDAT Ersatzwerte (56)": "SELECT COUNT(*) > 0 FROM sdat_series WHERE observations LIKE '%;56%'",
    "Abrechnung Entwurf, freigegeben, storniert": "SELECT COUNT(DISTINCT status) = 3 FROM billing_runs",
    "Freigabe mit Begründung": "SELECT COUNT(*) > 0 FROM billing_runs WHERE release_override_reason != ''",
    "zwei Läufe für denselben Zeitraum": "SELECT MAX(version) >= 2 FROM billing_runs",
    "Lauf über zwei Tarife": "SELECT COUNT(*) > 0 FROM billing_runs WHERE tariff_name LIKE '% / %'",
    "Rechnung und Gutschrift": "SELECT COUNT(DISTINCT doc_type) = 2 FROM billing_documents",
    "QR-Rechnung": "SELECT COUNT(*) > 0 FROM billing_documents WHERE qr_payload != ''",
    "Zahlung, Rückerstattung, Auszahlung": "SELECT COUNT(DISTINCT kind) = 3 FROM document_payments",
    "Mahnung und verworfene Mahnung": "SELECT COUNT(*) = 2 FROM document_reminders",
    "Mahnsperre": "SELECT COUNT(*) > 0 FROM billing_documents WHERE reminder_block_reason != ''",
    "Storno mit Stornobeleg": "SELECT COUNT(*) > 0 FROM billing_documents WHERE status = 'cancelled' AND cancel_number IS NOT NULL",
    "Ersatzbeleg": "SELECT COUNT(*) > 0 FROM billing_documents WHERE replaces_document_id IS NOT NULL",
    "Versand E-Mail und Papier": "SELECT COUNT(DISTINCT method) = 2 FROM document_deliveries",
    "Versand durch Shareomat": "SELECT COUNT(*) > 0 FROM document_deliveries WHERE sent_by = 'shareomat'",
    "Automatik-Schalter gespeichert": "SELECT COUNT(*) = 3 FROM settings WHERE setting_key LIKE 'auto_%_invoices'"
                                      " OR setting_key = 'auto_create_billing'",
    "Geheimnis in settings (B12)": "SELECT COUNT(*) > 0 FROM settings WHERE setting_key = 'entsoe_api_token'"
                                   " AND setting_value != ''",
    "Zahlungsprofil": "SELECT COUNT(*) > 0 FROM settings WHERE setting_key = 'invoice_payment_profile'",
    "E-Mail-Vorlage": "SELECT COUNT(*) > 0 FROM settings WHERE setting_key LIKE 'email_template.%'",
    "Sitzungen aktiv, angemeldet bleiben, widerrufen": "SELECT COUNT(*) >= 4 FROM auth_sessions",
    "Login-Versuche erfolgreich und fehlgeschlagen": "SELECT COUNT(DISTINCT success) = 2 FROM auth_attempts",
    "Live-Quellen testing, active, retired": "SELECT COUNT(DISTINCT state) = 3 FROM live_sources",
    "Messpunkt ohne Messung": "SELECT COUNT(*) > 0 FROM live_meter_states",
    "externe Daten": "SELECT (SELECT COUNT(*) FROM supplier_tariffs) * (SELECT COUNT(*) FROM reference_prices)"
                     " * (SELECT COUNT(*) FROM elcom_tariffs) * (SELECT COUNT(*) FROM exchange_rates)"
                     " * (SELECT COUNT(*) FROM data_imports) > 0",
    "Altbestand Messwert-Kopie und Ergebnisse": "SELECT (SELECT COUNT(*) FROM meter_readings) > 0"
                                                " AND (SELECT COUNT(*) FROM settlement_cycle_records) > 0",
    "Altbestand Prognosen": "SELECT (SELECT COUNT(*) FROM consumption_forecasts) > 0"
                            " AND (SELECT COUNT(*) FROM day_ahead_prices) > 0",
}


@pytest.mark.parametrize("state", sorted(STATES))
def test_fixture_contains_the_state(frozen, state):
    assert _one(frozen, STATES[state]) == 1, state


def test_fixture_is_synthetic_only(frozen):
    conn = sqlite3.connect(f"file:{frozen.as_posix()}?mode=ro", uri=True)
    try:
        emails = [r[0] for r in conn.execute("SELECT email FROM participants")]
        meters = [r[0] for r in conn.execute("SELECT meter_id FROM meters")]
        towns = {r[0] for r in conn.execute("SELECT city FROM participants")}
    finally:
        conn.close()
    assert all(e.endswith("@example.ch") for e in emails)
    assert all(m.startswith("CH9999999999999999999999") for m in meters)
    assert towns <= {"Testdorf", "Neudorf"}


@pytest.mark.skipif(sqlite_module._SCHEMA_VERSION != 18, reason="the generator needs the schema-18 code (frozen since)")
def test_generator_reproduces_the_frozen_fixture(tmp_path):
    built = build_schema18(tmp_path / "build")
    assert content_fingerprint(built.db_path) == (FIXTURE / "fingerprint.txt").read_text(encoding="utf-8").strip()


def test_released_runs_of_the_frozen_schema18_installation_compute_the_same(tmp_path):
    """The released runs of the frozen installation recompute to the same result fingerprint.

    Proven once against the 0.6.30 fixture before it was rebuilt (27.11.2026): billing unchanged by B28.
    Since then a regression guard: no later change may alter released results unnoticed.
    """
    db = tmp_path / "shareomat.db"
    with gzip.open(FIXTURE / "shareomat-schema18.db.gz", "rb") as src, open(db, "wb") as dst:
        shutil.copyfileobj(src, dst)
    init_db(db)                                                # adds the 0.6.31 columns, nothing else
    for name in ("inbox", "archive", "reports", "state"):
        (tmp_path / name).mkdir()
    runtime = RuntimeConfig(paths=PathConfig(**{n: tmp_path / n for n in ("inbox", "archive", "reports", "state")}))
    get_state().register_db(db)
    get_state().register_runtime(runtime)
    conn = sqlite3.connect(db)
    released = [r[0] for r in conn.execute("SELECT id FROM billing_runs WHERE status = 'released'")]
    conn.execute("UPDATE memberships SET review_note = ''")   # set after the releases; would block any recompute
    conn.commit()
    conn.close()
    assert len(released) == 3
    assert billing.released_runs_differing(db, runtime, released) == []
