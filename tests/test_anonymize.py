from synthesizer.anonymize import pseudonymize


def test_same_raw_id_maps_to_same_pseudonym():
    a = pseudonymize("imsi_sim_000001")
    b = pseudonymize("imsi_sim_000001")
    assert a == b


def test_different_raw_ids_map_to_different_pseudonyms():
    a = pseudonymize("imsi_sim_000001")
    b = pseudonymize("imsi_sim_000002")
    assert a != b


def test_pseudonym_never_contains_raw_id():
    raw = "imsi_sim_000042"
    pseudo = pseudonymize(raw)
    assert raw not in pseudo
    assert pseudo.startswith("ue_")
