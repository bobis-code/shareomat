# -*- coding: utf-8 -*-
"""Tests for deriving a participant's LEG role from its meters."""

from shareomat.core.participant_roles import participant_roles, roles_by_participant
from shareomat.models.meter import Meter


def test_no_meters_means_no_role():
    roles = participant_roles([])
    assert (roles.produces, roles.consumes, roles.storage) == (False, False, False)
    assert roles.label == "kein Messpunkt"


def test_separate_pv_and_consumption_meters_make_a_prosumer():
    roles = participant_roles([
        Meter("CH-PV", "p1", "PV", "producer"),
        Meter("CH-BEZUG", "p1", "Bezug", "consumer"),
    ])
    assert roles.produces and roles.consumes and not roles.storage
    assert roles.label == "Erzeuger & Verbraucher"
    assert roles.meter_ids == ("CH-PV", "CH-BEZUG")


def test_inactive_and_grid_meters_are_ignored():
    roles = participant_roles([
        Meter("CH-PV", "p1", "PV", "producer", active=False),
        Meter("CH-NETZ", "p1", "Netz", "grid"),
        Meter("CH-BEZUG", "p1", "Bezug", "consumer"),
    ])
    assert roles.label == "Verbraucher"
    assert roles.meter_ids == ("CH-BEZUG",)


def test_storage_meter_marks_storage_operator():
    roles = participant_roles([Meter("CH-BAT", "p1", "Batterie", "storage")])
    assert roles.storage and roles.produces and roles.consumes
    assert roles.label == "Speicherbetreiber"


def test_roles_grouped_per_participant():
    roles = roles_by_participant([
        Meter("A", "p1", "PV", "producer"),
        Meter("B", "p2", "Bezug", "consumer"),
    ])
    assert roles["p1"].label == "Erzeuger"
    assert roles["p2"].label == "Verbraucher"
