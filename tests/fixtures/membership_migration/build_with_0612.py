"""Build the schema-17 reference scenario WITH THE 0.6.12 CODE and record its billing results.

Run with PYTHONPATH pointing at the 0.6.12 worktree. Output: <out>/v17_scenario.db,
<out>/inbox/*.csv and <out>/expected.json (eligibility per day, preview fingerprints,
released run). Synthetic data only.
"""
import csv
import json
import shutil
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import shareomat.database.contract_versions as cvm
from shareomat.config import PathConfig, RuntimeConfig
from shareomat.database.billing import _result_fingerprint, compute_billing_preview, release_billing_run, save_draft
from shareomat.database.billing_eligibility import EligibilityResolver
from shareomat.database.community import save_community
from shareomat.database.contract_versions import publish_version, save_draft_version, withdraw_version
from shareomat.database.meters import create_meter
from shareomat.database.participant_contract import (
    assign_contract, list_assignments_for_version, mark_accepted, mark_declaration_issued, mark_notified,
    mark_vnb_confirmed, mark_vnb_notified, record_departure,
)
from shareomat.database.participants import create_participant
from shareomat.database.sqlite import init_db
from shareomat.models.community import Community
from shareomat.models.contract import ContractVersion
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant

out = Path(sys.argv[1])
CONFLICT = "--clean" not in sys.argv
sys.argv = [a for a in sys.argv if a != "--clean"]
if out.exists():
    shutil.rmtree(out)
(out / "inbox").mkdir(parents=True)
for name in ("archive", "reports", "state"):
    (out / "work" / name).mkdir(parents=True)
db = out / "v17_scenario.db"
init_db(db)
save_community(db, Community(community_id="ZEV-TEST", name="Test-LEG", address_line="Testweg 1",
                             postal_code="9999", city="Testdorf", vnb_confirmed_start=date(2026, 1, 1)))


def fixed(value):
    class F(date):
        @classmethod
        def today(cls):
            return value
    return F


def draft(valid_from, **kw):
    fields = dict(community_id="", version=0, status="draft", contract_text_snapshot="<p>Vertrag</p>",
                  local_rate_chf_kwh=Decimal("0.11"), feed_in_rate_chf_kwh=Decimal("0.105"),
                  admin_fee_chf_kwh=Decimal("0.005"), grid_operator="EBL", representative_name="Test Vertretung",
                  representative_address_line="Testweg 1", representative_postal_code="9999",
                  representative_city="Testdorf", representative_email="vertretung@example.ch",
                  valid_from=valid_from)
    fields.update(kw)
    return ContractVersion(**fields)


METERS = {
    "prod": ("CH00000000000000000000000000TEST01", "producer"),
    "c_upload": ("CH00000000000000000000000000TEST02", "consumer"),
    "c_pending": ("CH00000000000000000000000000TEST03", "consumer"),
    "c_unsigned": ("CH00000000000000000000000000TEST04", "consumer"),
    "c_left": ("CH00000000000000000000000000TEST05", "consumer"),
    "c_new": ("CH00000000000000000000000000TEST06", "consumer"),
}
for pid, (mid, role) in METERS.items():
    create_participant(db, Participant(pid, "Test " + pid, email=f"{pid}@example.ch", address_line="Testweg 2",
                                       postal_code="9999", city="Testdorf"))
    create_meter(db, Meter(mid, pid, "Zähler " + pid, role))

# v1 (founding), published long before it starts
cvm.date = fixed(date(2025, 6, 1))
v1 = publish_version(db, save_draft_version(db, draft(date(2026, 1, 1))).id)
rows = {pid: assign_contract(db, pid, v1.id, v1.tariff_id,
                             joined_at=date(2026, 3, 1) if pid == "c_unsigned" else date(2026, 1, 1))
        for pid in ("prod", "c_upload", "c_pending", "c_unsigned", "c_left")}
mark_declaration_issued(db, rows["prod"].id, date(2025, 11, 1), snapshot="<p>Erklärung prod</p>")
mark_accepted(db, rows["prod"].id, date(2025, 12, 1), method="paper", signed_by="Test prod", recorded_by="admin",
              paper_location="Ordner LEG, Register 1", snapshot="<p>Erklärung prod</p>")
mark_vnb_notified(db, rows["prod"].id, date(2025, 10, 1))
mark_vnb_confirmed(db, rows["prod"].id, date(2025, 12, 15), date(2026, 1, 1))
mark_accepted(db, rows["c_upload"].id, date(2025, 12, 2), method="scan", signed_by="Test c_upload",
              document_location="ab" * 32 + ".pdf", document_sha256="ab" * 32, snapshot="<p>Erklärung c_upload</p>")
mark_vnb_notified(db, rows["c_upload"].id, date(2025, 11, 1))
mark_vnb_confirmed(db, rows["c_upload"].id, date(2026, 1, 20), date(2026, 2, 1))
mark_accepted(db, rows["c_pending"].id, date(2025, 12, 3), method="paper", paper_location="Ordner LEG, Register 3")
mark_vnb_notified(db, rows["c_pending"].id, date(2025, 12, 5))
mark_declaration_issued(db, rows["c_unsigned"].id, date(2026, 2, 1), snapshot="<p>Erklärung c_unsigned</p>")
mark_accepted(db, rows["c_left"].id, date(2025, 12, 4), method="paper", paper_location="Ordner LEG, Register 5")
mark_vnb_notified(db, rows["c_left"].id, date(2025, 11, 2))
mark_vnb_confirmed(db, rows["c_left"].id, date(2025, 12, 16), date(2026, 1, 1))
record_departure(db, rows["c_left"].id, date(2026, 6, 30), reason="Wegzug")

# v2: contract change (6 months), carried members
cvm.date = fixed(date(2026, 3, 1))
v2 = publish_version(db, save_draft_version(db, draft(date(2026, 10, 1), representative_city="Neudorf")).id)
v2rows = {a.participant_id: a for a in list_assignments_for_version(db, v2.id)}
mark_notified(db, v2rows["prod"].id, date(2026, 3, 5), evidence="E-Mail vom 05.03.2026")
# P1: work done on the v1 rows after v2 was published
if CONFLICT:
    record_departure(db, rows["c_pending"].id, date(2026, 12, 31), reason="Kündigung")
    mark_vnb_confirmed(db, rows["c_pending"].id, date(2026, 3, 20), date(2026, 4, 1))
    mark_accepted(db, rows["c_unsigned"].id, date(2026, 3, 10), method="paper", paper_location="Ordner LEG, Register 4")
new = assign_contract(db, "c_new", v2.id, v2.tariff_id, joined_at=date(2026, 10, 1))
mark_accepted(db, new.id, date(2026, 9, 15), method="paper", paper_location="Ordner LEG, Register 6")
mark_vnb_notified(db, new.id, date(2026, 6, 1))
mark_vnb_confirmed(db, new.id, date(2026, 9, 1), date(2026, 10, 1))

# v3: price change with consent of all
cvm.date = fixed(date(2026, 9, 1))
v3 = publish_version(db, save_draft_version(db, draft(date(2027, 1, 1), local_rate_chf_kwh=Decimal("0.12"),
                                                       feed_in_rate_chf_kwh=Decimal("0.115"))).id,
                     consent_of_all=True)
v3rows = {a.participant_id: a for a in list_assignments_for_version(db, v3.id)}
mark_notified(db, v3rows["prod"].id, date(2026, 9, 10), evidence="Zustimmung unterschrieben")
mark_notified(db, v3rows["c_new"].id, date(2026, 9, 11), evidence="Zustimmung per Brief")

# v4: announced, a declaration handed out, then withdrawn -> archived
cvm.date = fixed(date(2026, 11, 10))
v4 = publish_version(db, save_draft_version(db, draft(date(2027, 4, 1), local_rate_chf_kwh=Decimal("0.13"),
                                                       feed_in_rate_chf_kwh=Decimal("0.125"))).id)
v4rows = {a.participant_id: a for a in list_assignments_for_version(db, v4.id)}
mark_declaration_issued(db, v4rows["c_new"].id, date(2026, 11, 11), snapshot="<p>Erklärung v4</p>")
withdraw_version(db, v4.id)
cvm.date = date


def write_csv(name, days, slots=(10, 11, 12, 13)):
    with (out / "inbox" / name).open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "meter_id", "value_kwh", "direction"])
        for d in days:
            for h in slots:
                ts = f"{d.isoformat()}T{h:02d}:00:00+00:00"
                w.writerow([ts, METERS["prod"][0], "3.0", "export"])
                for pid in ("c_upload", "c_pending", "c_unsigned", "c_left", "c_new"):
                    w.writerow([ts, METERS[pid][0], "0.5", "import"])


write_csv("2026-03.csv", [date(2026, 3, 2), date(2026, 3, 16)])
write_csv("2026-11.csv", [date(2026, 11, 2), date(2026, 11, 20)])
write_csv("2027-02.csv", [date(2027, 2, 3)])
runtime = RuntimeConfig(paths=PathConfig(inbox=out / "inbox", archive=out / "work" / "archive",
                                         reports=out / "work" / "reports", state=out / "work" / "state"))

previews = {}
for start, end in ((date(2026, 3, 1), date(2026, 3, 31)), (date(2026, 11, 1), date(2026, 11, 30)),
                   (date(2027, 2, 1), date(2027, 2, 28))):
    p = compute_billing_preview(db, runtime, start, end, persist_readings=False)
    previews[start.isoformat()] = {"fingerprint": _result_fingerprint(p),
                                  "lines": {i.participant_id: str(i.local_amount_chf) for i in p.line_items},
                                  "hard": p.availability.blocking_issues}
march = compute_billing_preview(db, runtime, date(2026, 3, 1), date(2026, 3, 31))
run = save_draft(db, march)
released = release_billing_run(db, run.id, override_reason="Testabrechnung (synthetisch)", runtime=runtime)


def eligibility(path):
    res = EligibilityResolver(path)
    result = {}
    d = date(2025, 12, 1)
    while d <= date(2027, 11, 30):
        e = res.for_day(d)
        result[d.isoformat()] = {"meters": e.meters, "excluded": e.excluded,
                                 "unconfirmed": sorted(e.unconfirmed_participants)}
        d += timedelta(days=1)
    return result


expected = {"eligibility": eligibility(db), "previews": previews,
            "released_run": {"id": released.id, "status": released.status,
                             "fingerprint": released.result_fingerprint}}
(out / "expected.json").write_text(json.dumps(expected, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
print("released run", released.id, released.status, released.result_fingerprint[:12])
print("previews", {k: v["fingerprint"][:12] for k, v in previews.items()})
if len(sys.argv) > 2:   # also record the old eligibility of a real database copy (read-only use)
    real = Path(sys.argv[2])
    (out / "real_expected.json").write_text(json.dumps(eligibility(real), ensure_ascii=False, sort_keys=True),
                                            encoding="utf-8")
    print("real eligibility recorded")
