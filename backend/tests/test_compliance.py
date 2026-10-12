from datetime import date

from app.services import compliance as cz


def test_auto_updates_need_the_setting_not_just_the_timer():
    timers_only = {"auto_updates": "enabled\nenabled\nnot-found", "auto_updates_config": ""}
    assert cz.check_auto_security_updates(timers_only, {})[0] is False

    apt_configured = {"auto_updates_config": 'APT::Periodic::Unattended-Upgrade "1";'}
    assert cz.check_auto_security_updates(apt_configured, {})[0] is True

    apt_disabled = {"auto_updates_config": 'APT::Periodic::Unattended-Upgrade "0";'}
    assert cz.check_auto_security_updates(apt_disabled, {})[0] is False

    dnf_applies = {"auto_updates_config": "apply_updates = yes"}
    assert cz.check_auto_security_updates(dnf_applies, {})[0] is True


def test_refresh_failure_makes_pending_updates_unknown():
    failed = {"security_updates": "refresh=failed\n0"}
    assert cz.check_security_updates_pending(failed, {})[0] is None

    pending = {"security_updates": "refresh=ok\n7"}
    assert cz.check_security_updates_pending(pending, {})[0] is False

    none_pending = {"security_updates": "refresh=ok\n0"}
    assert cz.check_security_updates_pending(none_pending, {})[0] is True


def test_missing_facts_are_unknown_not_failed():
    assert cz.check_security_updates_pending({}, {})[0] is None
    assert cz.check_ssh_password_disabled({}, {})[0] is None
    assert cz.check_reboot_not_pending({}, {})[0] is None


def test_end_of_support_uses_the_lifecycle_table():
    facts = {"os_release": 'ID=debian\nVERSION_ID="9"\n'}
    passed, detail = cz.check_os_supported(facts, {})
    assert passed is False and "2022-06-30" in detail

    facts = {"os_release": 'ID=debian\nVERSION_ID="13"\n'}
    assert cz.check_os_supported(facts, {})[0] is (date.today() <= cz.EOL_DATES[("debian", "13")])


def test_framework_score_ignores_unknown_requirements():
    checks = {cid: {"passed": True} for cid in cz.CHECKS}
    checks["ssh_password_disabled"] = {"passed": None}
    frameworks = cz.score_host(checks)
    cis = frameworks["cis"]
    assert cis["not_assessed"] >= 1
    assert cis["score"] == round(100 * cis["passed"] / (cis["passed"] + cis["failed"]), 1)
