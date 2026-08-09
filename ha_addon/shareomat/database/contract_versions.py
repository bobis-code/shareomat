# -*- coding: utf-8 -*-
"""
File: shareomat/database/contract_versions.py

Purpose:
    Versioned LEG contract terms — the source of truth for LEG prices.
    A draft is freely editable (save/update/delete, no notice-period
    checks). Publishing a draft snapshots it as immutable, checks the
    contractually required notice period against whichever version is
    currently in force or announced, and creates the tariff it describes.
    A published version whose valid_from is still in the future can be
    withdrawn (undoing exactly what publishing did); a published version
    already in force can only be closed out via end_contract(), never
    edited or deleted.

    Only two stored statuses exist: "draft" and "published". Whether a
    published version is currently in force, merely announced, or
    historical is derived from valid_from/valid_until at read time —
    exactly like shareomat.database.tariffs.get_tariff_for_date() — never
    a third stored status and never a scheduler.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import calendar
import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from shareomat.database.community import ensure_community_row_id
from shareomat.database.sqlite import connect, date_to_str, now_iso, str_to_date
from shareomat.leg_const import CONTRACT_STATUS_DRAFT, CONTRACT_STATUS_PUBLISHED
from shareomat.models.contract import ContractVersion


class ContractWorkflowError(Exception):
    """Raised for invalid contract-version requests (bad status transition, notice period, not found)."""


def _add_months(value: date, months: int) -> date:
    """Add whole months to a date, clamping the day to the target month's length (e.g. 31 Jan + 1 = 28/29 Feb)."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _row_to_version(row: sqlite3.Row, community_id: str) -> ContractVersion:
    return ContractVersion(
        community_id=community_id,
        version=row["version"],
        status=row["status"],
        contract_text_snapshot=row["contract_text_snapshot"],
        vnb_reference_price_chf_kwh=Decimal(row["vnb_reference_price_chf_kwh"]),
        price_reduction_chf_kwh=Decimal(row["price_reduction_chf_kwh"]),
        vnb_reference_price_nt_chf_kwh=(
            Decimal(row["vnb_reference_price_nt_chf_kwh"]) if row["vnb_reference_price_nt_chf_kwh"] else None
        ),
        price_reduction_nt_chf_kwh=(
            Decimal(row["price_reduction_nt_chf_kwh"]) if row["price_reduction_nt_chf_kwh"] else None
        ),
        local_rate_chf_kwh=Decimal(row["local_rate_chf_kwh"]),
        local_rate_nt_chf_kwh=Decimal(row["local_rate_nt_chf_kwh"]) if row["local_rate_nt_chf_kwh"] else None,
        feed_in_rate_chf_kwh=Decimal(row["feed_in_rate_chf_kwh"]),
        feed_in_rate_nt_chf_kwh=(
            Decimal(row["feed_in_rate_nt_chf_kwh"]) if row["feed_in_rate_nt_chf_kwh"] else None
        ),
        admin_fee_chf_kwh=Decimal(row["admin_fee_chf_kwh"]),
        rate_mode=row["rate_mode"],
        representative_name=row["representative_name"],
        representative_address_line=row["representative_address_line"],
        representative_postal_code=row["representative_postal_code"],
        representative_city=row["representative_city"],
        representative_email=row["representative_email"],
        grid_operator=row["grid_operator"],
        distribution_method=row["distribution_method"],
        price_notice_period_months=row["price_notice_period_months"],
        contract_notice_period_months=row["contract_notice_period_months"],
        valid_from=str_to_date(row["valid_from"]),
        valid_until=str_to_date(row["valid_until"]),
        tariff_id=row["tariff_id"],
        supersedes_version_id=row["supersedes_version_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        id=row["id"],
    )


def _community_id(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT community_id FROM communities LIMIT 1").fetchone()
    return row["community_id"] if row else ""


def list_contract_versions(db_path: Path) -> list[ContractVersion]:
    """Return all contract versions, newest version first."""
    with connect(db_path) as conn:
        community_id = _community_id(conn)
        if not community_id:
            return []
        rows = conn.execute("SELECT * FROM contract_versions ORDER BY version DESC").fetchall()
        return [_row_to_version(r, community_id) for r in rows]


def get_contract_version(db_path: Path, version_id: int) -> ContractVersion | None:
    """Return one contract version by its internal database id, or None if it does not exist."""
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM contract_versions WHERE id = ?", (version_id,)).fetchone()
        if row is None:
            return None
        return _row_to_version(row, _community_id(conn))


def get_contract_version_for_date(db_path: Path, as_of: date | None = None) -> ContractVersion | None:
    """Return the published contract version in force on `as_of` (default today), or None.

    Mirrors shareomat.database.tariffs.get_tariff_for_date() exactly — a
    published version whose valid_from lies in the future is simply not
    matched yet, no matter how long ago it was published.
    """
    as_of = as_of or date.today()
    with connect(db_path) as conn:
        community_id = _community_id(conn)
        if not community_id:
            return None
        row = conn.execute(
            """
            SELECT * FROM contract_versions
            WHERE status = ? AND valid_from <= ? AND (valid_until IS NULL OR valid_until >= ?)
            ORDER BY valid_from DESC LIMIT 1
            """,
            (CONTRACT_STATUS_PUBLISHED, date_to_str(as_of), date_to_str(as_of)),
        ).fetchone()
        return _row_to_version(row, community_id) if row else None


def get_latest_relevant_version(db_path: Path) -> ContractVersion | None:
    """Return the published version with the latest valid_from — in force or merely announced.

    This is "what the participants most recently agreed to", the baseline
    a new draft is diffed against in publish_version() to pick the
    required notice period — independent of whether that version has
    actually taken effect yet.
    """
    with connect(db_path) as conn:
        community_id = _community_id(conn)
        if not community_id:
            return None
        row = conn.execute(
            "SELECT * FROM contract_versions WHERE status = ? ORDER BY valid_from DESC LIMIT 1",
            (CONTRACT_STATUS_PUBLISHED,),
        ).fetchone()
        return _row_to_version(row, community_id) if row else None


_EDITABLE_COLUMNS = (
    "vnb_reference_price_chf_kwh", "price_reduction_chf_kwh",
    "vnb_reference_price_nt_chf_kwh", "price_reduction_nt_chf_kwh",
    "local_rate_chf_kwh", "local_rate_nt_chf_kwh", "feed_in_rate_chf_kwh", "feed_in_rate_nt_chf_kwh",
    "admin_fee_chf_kwh", "rate_mode",
    "representative_name", "representative_address_line", "representative_postal_code",
    "representative_city", "representative_email", "grid_operator", "distribution_method",
    "price_notice_period_months", "contract_notice_period_months",
)


def _editable_values(version: ContractVersion) -> tuple:
    return (
        str(version.vnb_reference_price_chf_kwh),
        str(version.price_reduction_chf_kwh),
        str(version.vnb_reference_price_nt_chf_kwh) if version.vnb_reference_price_nt_chf_kwh is not None else None,
        str(version.price_reduction_nt_chf_kwh) if version.price_reduction_nt_chf_kwh is not None else None,
        str(version.local_rate_chf_kwh),
        str(version.local_rate_nt_chf_kwh) if version.local_rate_nt_chf_kwh is not None else None,
        str(version.feed_in_rate_chf_kwh),
        str(version.feed_in_rate_nt_chf_kwh) if version.feed_in_rate_nt_chf_kwh is not None else None,
        str(version.admin_fee_chf_kwh),
        version.rate_mode,
        version.representative_name,
        version.representative_address_line,
        version.representative_postal_code,
        version.representative_city,
        version.representative_email,
        version.grid_operator,
        version.distribution_method,
        version.price_notice_period_months,
        version.contract_notice_period_months,
    )


def save_draft_version(db_path: Path, version: ContractVersion) -> ContractVersion:
    """Insert a new draft (version = previous max + 1). No notice-period or date validation."""
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            community_row_id = ensure_community_row_id(conn)
            version_number = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM contract_versions WHERE community_id = ?",
                (community_row_id,),
            ).fetchone()["v"]
            columns = ", ".join(_EDITABLE_COLUMNS)
            placeholders = ", ".join("?" * len(_EDITABLE_COLUMNS))
            cursor = conn.execute(
                f"""
                INSERT INTO contract_versions
                    (community_id, version, status, contract_text_snapshot, valid_from, valid_until,
                     {columns}, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, {placeholders}, ?, ?)
                """,
                (
                    community_row_id, version_number, CONTRACT_STATUS_DRAFT, version.contract_text_snapshot,
                    date_to_str(version.valid_from), date_to_str(version.valid_until),
                    *_editable_values(version), now, now,
                ),
            )
            new_id = cursor.lastrowid
    result = get_contract_version(db_path, new_id)
    assert result is not None
    return result


def update_draft_version(db_path: Path, version_id: int, version: ContractVersion) -> ContractVersion:
    """Replace all editable fields of an existing draft in place. Only allowed while status == draft."""
    with connect(db_path) as conn:
        with conn:
            row = conn.execute("SELECT status FROM contract_versions WHERE id = ?", (version_id,)).fetchone()
            if row is None:
                raise ContractWorkflowError(f"Vertragsversion {version_id} wurde nicht gefunden.")
            if row["status"] != CONTRACT_STATUS_DRAFT:
                raise ContractWorkflowError(
                    f"Nur Entwürfe können bearbeitet werden (aktueller Status: {row['status']})."
                )
            assignments = ", ".join(f"{col} = ?" for col in _EDITABLE_COLUMNS)
            conn.execute(
                f"""
                UPDATE contract_versions
                SET contract_text_snapshot = ?, valid_from = ?, valid_until = ?, {assignments}, updated_at = ?
                WHERE id = ?
                """,
                (
                    version.contract_text_snapshot, date_to_str(version.valid_from), date_to_str(version.valid_until),
                    *_editable_values(version), now_iso(), version_id,
                ),
            )
    result = get_contract_version(db_path, version_id)
    assert result is not None
    return result


def delete_draft(db_path: Path, version_id: int) -> None:
    """Delete a draft version. Refuses anything that has ever been published."""
    with connect(db_path) as conn:
        with conn:
            row = conn.execute("SELECT status FROM contract_versions WHERE id = ?", (version_id,)).fetchone()
            if row is None:
                raise ContractWorkflowError(f"Vertragsversion {version_id} wurde nicht gefunden.")
            if row["status"] != CONTRACT_STATUS_DRAFT:
                raise ContractWorkflowError(
                    "Nur Entwürfe können gelöscht werden — veröffentlichte Versionen bleiben in der Historie erhalten."
                )
            conn.execute("DELETE FROM contract_versions WHERE id = ?", (version_id,))


def _required_notice_months(draft: ContractVersion, prior: ContractVersion | None) -> int:
    """Determine the required notice period by diffing `draft` against the prior in-force/announced version.

    Only price fields changed -> the prior version's price_notice_period_months.
    Any master-data field or either notice period itself changed -> the
    prior version's contract_notice_period_months. Both -> the stricter
    (larger) of the two. No prior version (first-ever publish) -> the
    draft's own price_notice_period_months, since there is nothing to diff.
    """
    if prior is None:
        return draft.price_notice_period_months

    price_changed = (
        draft.local_rate_chf_kwh != prior.local_rate_chf_kwh
        or draft.local_rate_nt_chf_kwh != prior.local_rate_nt_chf_kwh
        or draft.feed_in_rate_chf_kwh != prior.feed_in_rate_chf_kwh
        or draft.feed_in_rate_nt_chf_kwh != prior.feed_in_rate_nt_chf_kwh
        or draft.admin_fee_chf_kwh != prior.admin_fee_chf_kwh
        or draft.rate_mode != prior.rate_mode
    )
    contract_changed = (
        draft.representative_name != prior.representative_name
        or draft.representative_address_line != prior.representative_address_line
        or draft.representative_postal_code != prior.representative_postal_code
        or draft.representative_city != prior.representative_city
        or draft.representative_email != prior.representative_email
        or draft.grid_operator != prior.grid_operator
        or draft.distribution_method != prior.distribution_method
        or draft.price_notice_period_months != prior.price_notice_period_months
        or draft.contract_notice_period_months != prior.contract_notice_period_months
    )

    required = 0
    if price_changed:
        required = max(required, prior.price_notice_period_months)
    if contract_changed:
        required = max(required, prior.contract_notice_period_months)
    if not price_changed and not contract_changed:
        required = prior.price_notice_period_months
    return required


def publish_version(db_path: Path, version_id: int) -> ContractVersion:
    """Publish a draft: enforce the contractually required notice period, create its tariff.

    Raises ContractWorkflowError if: the version is not a draft, valid_from
    is unset or too soon (message names the earliest allowed date), or no
    existing tariff provides a grid_rate_chf_kwh to carry forward (no
    silent 0-fallback). On success, in one transaction: the prior in-force/
    announced version (if any) gets valid_until capped the day before this
    version's valid_from, its tariff likewise; a new tariff is created from
    this version's prices; this version becomes published, recording
    tariff_id/supersedes_version_id so withdraw_version() can undo it.
    """
    with connect(db_path) as conn:
        with conn:
            community_id = _community_id(conn)
            row = conn.execute("SELECT * FROM contract_versions WHERE id = ?", (version_id,)).fetchone()
            if row is None:
                raise ContractWorkflowError(f"Vertragsversion {version_id} wurde nicht gefunden.")
            if row["status"] != CONTRACT_STATUS_DRAFT:
                raise ContractWorkflowError(
                    f"Nur Entwürfe können veröffentlicht werden (aktueller Status: {row['status']})."
                )
            draft = _row_to_version(row, community_id)

            prior_row = conn.execute(
                "SELECT * FROM contract_versions WHERE status = ? ORDER BY valid_from DESC LIMIT 1",
                (CONTRACT_STATUS_PUBLISHED,),
            ).fetchone()
            prior = _row_to_version(prior_row, community_id) if prior_row else None

            if draft.valid_from is None:
                raise ContractWorkflowError('„Gültig ab" muss gesetzt sein, um eine Version zu veröffentlichen.')

            required_months = _required_notice_months(draft, prior)
            earliest = _add_months(date.today(), required_months)
            if draft.valid_from < earliest:
                raise ContractWorkflowError(
                    f"Erforderliche Ankündigungsfrist: {required_months} Monate. "
                    f"Frühestmögliches Inkrafttreten: {earliest.isoformat()}. "
                    f"Gewählt: {draft.valid_from.isoformat()}."
                )

            base_tariff_row = conn.execute(
                "SELECT grid_rate_chf_kwh FROM tariffs ORDER BY valid_from DESC, id DESC LIMIT 1"
            ).fetchone()
            if base_tariff_row is None:
                raise ContractWorkflowError(
                    "Kein bestehender Tarif für die Netznutzung gefunden — zuerst über die "
                    "Tarife-Seite einen Ausgangstarif mit Netznutzungssatz anlegen."
                )
            grid_rate_chf_kwh = base_tariff_row["grid_rate_chf_kwh"]

            now = now_iso()
            community_row_id = ensure_community_row_id(conn)

            if prior is not None:
                closeout_until = draft.valid_from - timedelta(days=1)
                conn.execute(
                    "UPDATE contract_versions SET valid_until = ?, updated_at = ? WHERE id = ?",
                    (date_to_str(closeout_until), now, prior.id),
                )
                if prior.tariff_id is not None:
                    conn.execute(
                        "UPDATE tariffs SET valid_until = ?, updated_at = ? WHERE id = ?",
                        (date_to_str(closeout_until), now, prior.tariff_id),
                    )

            cursor = conn.execute(
                """
                INSERT INTO tariffs
                    (community_id, name, local_rate_chf_kwh, grid_rate_chf_kwh, feed_in_rate_chf_kwh,
                     admin_fee_chf_kwh, rate_mode, local_rate_nt_chf_kwh, feed_in_rate_nt_chf_kwh,
                     valid_from, valid_until, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    community_row_id, f"Vertrag v{draft.version}", str(draft.local_rate_chf_kwh),
                    grid_rate_chf_kwh, str(draft.feed_in_rate_chf_kwh), str(draft.admin_fee_chf_kwh),
                    draft.rate_mode,
                    str(draft.local_rate_nt_chf_kwh) if draft.local_rate_nt_chf_kwh is not None else None,
                    str(draft.feed_in_rate_nt_chf_kwh) if draft.feed_in_rate_nt_chf_kwh is not None else None,
                    date_to_str(draft.valid_from), date_to_str(draft.valid_until), 1, now, now,
                ),
            )
            new_tariff_id = cursor.lastrowid

            conn.execute(
                "UPDATE contract_versions SET status = ?, tariff_id = ?, supersedes_version_id = ?, "
                "updated_at = ? WHERE id = ?",
                (CONTRACT_STATUS_PUBLISHED, new_tariff_id, prior.id if prior else None, now, version_id),
            )
    result = get_contract_version(db_path, version_id)
    assert result is not None
    return result


def withdraw_version(db_path: Path, version_id: int) -> ContractVersion:
    """Undo publish_version(): only for a published version whose valid_from is still in the future.

    Deletes the tariff this version created (safe — it never took effect,
    so it was never used for a billing run), restores the previously
    superseded version's valid_until/tariff to open-ended, and turns this
    version back into a plain, editable draft.
    """
    with connect(db_path) as conn:
        with conn:
            community_id = _community_id(conn)
            row = conn.execute("SELECT * FROM contract_versions WHERE id = ?", (version_id,)).fetchone()
            if row is None:
                raise ContractWorkflowError(f"Vertragsversion {version_id} wurde nicht gefunden.")
            version = _row_to_version(row, community_id)
            if version.status != CONTRACT_STATUS_PUBLISHED:
                raise ContractWorkflowError(
                    f"Nur veröffentlichte Versionen können zurückgezogen werden (aktueller Status: {version.status})."
                )
            if version.valid_from is None or version.valid_from <= date.today():
                raise ContractWorkflowError(
                    'Diese Vertragsversion ist bereits wirksam und kann nicht zurückgezogen werden — '
                    'dafür „Vertrag beenden" verwenden.'
                )

            now = now_iso()
            tariff_id_to_delete = version.tariff_id
            supersedes_id = version.supersedes_version_id

            conn.execute(
                "UPDATE contract_versions SET status = ?, tariff_id = NULL, supersedes_version_id = NULL, "
                "updated_at = ? WHERE id = ?",
                (CONTRACT_STATUS_DRAFT, now, version_id),
            )
            if tariff_id_to_delete is not None:
                conn.execute("DELETE FROM tariffs WHERE id = ?", (tariff_id_to_delete,))
            if supersedes_id is not None:
                conn.execute(
                    "UPDATE contract_versions SET valid_until = NULL, updated_at = ? WHERE id = ?",
                    (now, supersedes_id),
                )
                prior_tariff_row = conn.execute(
                    "SELECT tariff_id FROM contract_versions WHERE id = ?", (supersedes_id,)
                ).fetchone()
                if prior_tariff_row and prior_tariff_row["tariff_id"] is not None:
                    conn.execute(
                        "UPDATE tariffs SET valid_until = NULL, updated_at = ? WHERE id = ?",
                        (now, prior_tariff_row["tariff_id"]),
                    )
    result = get_contract_version(db_path, version_id)
    assert result is not None
    return result


def end_contract(db_path: Path, version_id: int, end_date: date) -> ContractVersion:
    """Terminate the currently active contract version by setting valid_until — nothing is deleted."""
    with connect(db_path) as conn:
        with conn:
            community_id = _community_id(conn)
            row = conn.execute("SELECT * FROM contract_versions WHERE id = ?", (version_id,)).fetchone()
            if row is None:
                raise ContractWorkflowError(f"Vertragsversion {version_id} wurde nicht gefunden.")
            version = _row_to_version(row, community_id)
            if version.status != CONTRACT_STATUS_PUBLISHED or version.valid_from is None or version.valid_from > date.today():
                raise ContractWorkflowError("Nur ein aktuell aktiver Vertrag kann beendet werden.")

            now = now_iso()
            conn.execute(
                "UPDATE contract_versions SET valid_until = ?, updated_at = ? WHERE id = ?",
                (date_to_str(end_date), now, version_id),
            )
            if version.tariff_id is not None:
                conn.execute(
                    "UPDATE tariffs SET valid_until = ?, updated_at = ? WHERE id = ?",
                    (date_to_str(end_date), now, version.tariff_id),
                )
    result = get_contract_version(db_path, version_id)
    assert result is not None
    return result
