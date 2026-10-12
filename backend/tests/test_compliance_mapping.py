from app.services import compliance as cz


def test_every_requirement_points_at_real_checks():
    for fw_id, fw in cz.FRAMEWORKS.items():
        for req_id, title, check_ids in fw["requirements"]:
            assert check_ids, f"{fw_id} {req_id} has no checks"
            for check_id in check_ids:
                assert check_id in cz.CHECKS, f"{fw_id} {req_id} references unknown check {check_id}"
            assert title.strip(), f"{fw_id} {req_id} has no title"


def test_requirement_ids_are_unique_within_a_framework():
    for fw_id, fw in cz.FRAMEWORKS.items():
        ids = [req_id for req_id, _, _ in fw["requirements"]]
        assert len(ids) == len(set(ids)), f"duplicate requirement id in {fw_id}"


def test_every_check_is_used_by_some_requirement():
    used = {c for fw in cz.FRAMEWORKS.values() for _, _, checks in fw["requirements"] for c in checks}
    unused = set(cz.CHECKS) - used
    assert not unused, f"checks not mapped to any requirement: {sorted(unused)}"


def test_review_notes_refer_to_existing_requirements():
    for (fw_id, req_id), note in cz.MAPPING_NOTES.items():
        ids = {r for r, _, _ in cz.FRAMEWORKS[fw_id]["requirements"]}
        assert req_id in ids, f"note for unknown requirement {fw_id} {req_id}"
        assert note.strip()


def test_catalogue_marks_mapping_as_unvalidated():
    catalogue = cz.mapping_catalogue()
    assert catalogue["status"] == cz.MAPPING_STATUS
    assert all(fw["status"] == cz.MAPPING_STATUS for fw in catalogue["frameworks"])
