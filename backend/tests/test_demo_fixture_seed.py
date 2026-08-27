from scripts.seed_demo_fixture import DISCLAIMER, demo_pack_root, load_demo_pack


def test_clean_demo_fixture_uses_the_reviewed_scenario_library():
    pack = load_demo_pack()

    assert pack["synthetic"] is True
    assert pack["warning"] == DISCLAIMER
    assert pack["schema_version"] == 3
    assert pack["pack_version"] == "demo-scenario-library-v2"
    assert len(pack["matters"]) == 18
    filenames = [name for matter in pack["matters"] for name in matter["documents"]]
    assert len(filenames) == len(set(filenames)) == 75
    assert all((demo_pack_root() / name).is_file() for name in filenames)
    assert all(matter["demo_prompt"] for matter in pack["matters"])
    assert all(matter["suggested_tasks"] for matter in pack["matters"])
    assert all(
        matter["client_profile"]["secondary_contact"]["email"].endswith(".invalid")
        and matter["client_profile"]["client_since"]
        and matter["client_profile"]["preferred_contact_window"]
        for matter in pack["matters"]
    )
