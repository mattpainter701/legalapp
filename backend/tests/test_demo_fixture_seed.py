from scripts.seed_demo_fixture import DISCLAIMER, demo_pack_root, load_demo_pack


def test_clean_demo_fixture_uses_the_reviewed_three_matter_pack():
    pack = load_demo_pack()

    assert pack["synthetic"] is True
    assert pack["warning"] == DISCLAIMER
    assert len(pack["matters"]) == 3
    filenames = [name for matter in pack["matters"] for name in matter["documents"]]
    assert len(filenames) == len(set(filenames)) == 6
    assert all((demo_pack_root() / name).is_file() for name in filenames)
    assert all(matter["demo_prompt"] for matter in pack["matters"])
    assert all(matter["suggested_tasks"] for matter in pack["matters"])
