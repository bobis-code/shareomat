# -*- coding: utf-8 -*-
"""Synthetic SDAT-CH E66/E31 documents in the structure of the official VSE examples.

Structure (element names, namespace, nesting, code values) follows
ValidatedMeteredData_1p6 / AggregatedMeteredData_1p3 and the VSE example
messages 1_12_3 and 1_13_3 (SDAT-CH 2025, Stand 04.12.2025). Every ID and
value is synthetic; the official files themselves are not part of the repo
(licence not clarified).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from shareomat.core.pipeline.raw.sdat_ch import eic_check_char


def eic(first15: str) -> str:
    return first15 + eic_check_char(first15)


RECEIVER_EIC = eic("12X-TESTCEM0000")
SENDER_EIC = eic("12X-TESTVNB0000")
COMMUNITY = "100001-TEST01"
OTHER_COMMUNITY = "100002-TEST02"
CONSUMER = "CH1000000000000000000000000000001"
PRODUCER = "CH1000000000000000000000000000002"
PRODUCT_CODES = {
    "total": ("ebIXCode", "9", "8716867000030"),
    "local": ("VSENationalCode", "260", "2404050010123"),
    "residual": ("VSENationalCode", "260", "2404050010124"),
}


@dataclass
class Series:
    point: str                    # metering point (E66) or grid area EIC (E31)
    direction: str                # consumption | production
    energy_type: str              # total | local | residual
    values: list[str]
    conditions: list[str] | None = None
    community: str = COMMUNITY
    community_type: str = "CT01"
    start: datetime | None = None
    product_override: tuple[str, str, str] | None = None


@dataclass
class Document:
    document_type: str = "E66"
    series: list[Series] = field(default_factory=list)
    status: str = "9"
    created: datetime = datetime(2026, 12, 7, 5, 0, tzinfo=timezone.utc)
    receiver_eic: str = RECEIVER_EIC
    receiver_role: str = "CEM"
    business_reason: str = "C40"
    report_start: datetime | None = None
    report_end: datetime | None = None
    document_id: str = "TEST-DOC-1"
    ack: bool = True


def day_start(day: datetime) -> datetime:
    """UTC start of the local (Europe/Zurich) day that starts at `day` local midnight."""
    from zoneinfo import ZoneInfo
    local = day.replace(tzinfo=ZoneInfo("Europe/Zurich"))
    return local.astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _block(doc: Document, s: Series, index: int, default_start: datetime) -> str:
    start = s.start or default_start
    end = start + timedelta(minutes=15 * len(s.values))
    tag, agency, code = s.product_override or PRODUCT_CODES[s.energy_type]
    if doc.document_type == "E66":
        mp_tag = "ConsumptionMeteringPoint" if s.direction == "consumption" else "ProductionMeteringPoint"
        identity = (f'<rsm:{mp_tag}><rsm:VSENationalID schemeID="VSE" schemeAgencyID="260">{s.point}'
                    f'</rsm:VSENationalID></rsm:{mp_tag}>')
        criteria = ""
    else:
        identity = f'<rsm:MeteringGridArea><rsm:EICID schemeAgencyID="305">{s.point}</rsm:EICID></rsm:MeteringGridArea>'
        flow = "E17" if s.direction == "consumption" else "E18"
        criteria = (f'<rsm:AggregationCriteria><rsm:FlowCharacteristic listAgencyID="260">{flow}</rsm:FlowCharacteristic>'
                    '<rsm:SettlementMethodCharacteristic listAgencyID="260">E02</rsm:SettlementMethodCharacteristic>'
                    '</rsm:AggregationCriteria>')
    product = (f'<rsm:Product><rsm:ID><rsm:{tag} schemeAgencyID="{agency}">{code}</rsm:{tag}></rsm:ID>'
               '<rsm:MeasureUnit>KWH</rsm:MeasureUnit></rsm:Product>')
    community = (f'<rsm:Community><rsm:CommunityID>{s.community}</rsm:CommunityID><rsm:CommunityType codeListAgency="260">'
                 f'<rsm:VSENationalCode>{s.community_type}</rsm:VSENationalCode></rsm:CommunityType></rsm:Community>')
    conditions = s.conditions or [""] * len(s.values)
    observations = "".join(
        f'<rsm:Observation><rsm:Position><rsm:Sequence>{i + 1}</rsm:Sequence></rsm:Position>'
        f'<rsm:Volume>{v}</rsm:Volume>{f"<rsm:Condition>{c}</rsm:Condition>" if c else ""}</rsm:Observation>'
        for i, (v, c) in enumerate(zip(s.values, conditions))
    )
    order = (identity + product + criteria) if doc.document_type == "E31" else (identity + product)
    return (f'<rsm:MeteringData><rsm:DocumentID>{doc.document_id}-{index}</rsm:DocumentID>'
            f'<rsm:Interval><rsm:StartDateTime>{_iso(start)}</rsm:StartDateTime><rsm:EndDateTime>{_iso(end)}'
            '</rsm:EndDateTime></rsm:Interval><rsm:Resolution><rsm:Resolution>15</rsm:Resolution><rsm:Unit>MIN</rsm:Unit>'
            f'</rsm:Resolution>{order}{community}{observations}</rsm:MeteringData>')


def build(doc: Document, default_start: datetime | None = None) -> bytes:
    default_start = default_start or datetime(2026, 11, 30, 23, 0, tzinfo=timezone.utc)
    starts = [s.start or default_start for s in doc.series] or [default_start]
    ends = [(s.start or default_start) + timedelta(minutes=15 * len(s.values)) for s in doc.series] or [default_start]
    report_start = doc.report_start or min(starts)
    report_end = doc.report_end or max(ends)
    root = "ValidatedMeteredData_16" if doc.document_type == "E66" else "AggregatedMeteredData_13"
    header = root.split("_")[0] + "_HeaderInformation"
    blocks = "".join(_block(doc, s, i, default_start) for i, s in enumerate(doc.series, start=1))
    service = '<rsm:BusinessService><rsm:ServiceTransaction isIntelligibleCheckRequired="true"/></rsm:BusinessService>' if doc.ack else ""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<rsm:{root} xmlns:rsm="http://www.strom.ch" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<rsm:{header}><rsm:HeaderVersion>1.0</rsm:HeaderVersion>'
        f'<rsm:Sender><rsm:ID><rsm:EICID schemeAgencyID="305">{SENDER_EIC}</rsm:EICID></rsm:ID>'
        f'<rsm:Role>{"MDR" if doc.document_type == "E66" else "DEA"}</rsm:Role></rsm:Sender>'
        f'<rsm:Receiver><rsm:ID><rsm:EICID schemeAgencyID="305">{doc.receiver_eic}</rsm:EICID></rsm:ID>'
        f'<rsm:Role>{doc.receiver_role}</rsm:Role></rsm:Receiver>'
        '<rsm:InstanceDocument><rsm:DictionaryAgencyID>260</rsm:DictionaryAgencyID>'
        '<rsm:VersionID listAgencyID="260">2007B</rsm:VersionID>'
        f'<rsm:DocumentID>{doc.document_id}</rsm:DocumentID>'
        f'<rsm:DocumentType listAgencyID="260"><rsm:ebIXCode>{doc.document_type}</rsm:ebIXCode></rsm:DocumentType>'
        f'<rsm:Creation>{_iso(doc.created)}</rsm:Creation><rsm:Status>{doc.status}</rsm:Status></rsm:InstanceDocument>'
        '<rsm:BusinessScopeProcess><rsm:BusinessReasonType codeListAgency="260">'
        f'<rsm:VSENationalCode>{doc.business_reason}</rsm:VSENationalCode></rsm:BusinessReasonType>'
        '<rsm:BusinessDomainType listAgencyID="260">E02</rsm:BusinessDomainType>'
        '<rsm:BusinessSectorType>23</rsm:BusinessSectorType>'
        f'<rsm:ReportPeriod><rsm:StartDateTime>{_iso(report_start)}</rsm:StartDateTime>'
        f'<rsm:EndDateTime>{_iso(report_end)}</rsm:EndDateTime></rsm:ReportPeriod>{service}'
        f'</rsm:BusinessScopeProcess></rsm:{header}>{blocks}</rsm:{root}>'
    )
    return xml.encode("utf-8")


def leg_day(consumer_total: list[str], consumer_local: list[str], producer_total: list[str],
            producer_local: list[str], *, start: datetime | None = None, conditions: list[str] | None = None,
            community: str = COMMUNITY) -> list[Series]:
    """The six flows of a consumer and a producer; residual = total - local (as the grid operator computes it)."""
    from decimal import Decimal

    def residual(total, local):
        return [str(Decimal(t) - Decimal(l)) for t, l in zip(total, local)]

    out = []
    for point, direction, total, local in (
        (CONSUMER, "consumption", consumer_total, consumer_local),
        (PRODUCER, "production", producer_total, producer_local),
    ):
        out.append(Series(point, direction, "total", total, conditions, community, start=start))
        out.append(Series(point, direction, "local", local, conditions, community, start=start))
        out.append(Series(point, direction, "residual", residual(total, local), conditions, community, start=start))
    return out
