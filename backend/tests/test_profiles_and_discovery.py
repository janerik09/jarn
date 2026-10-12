from app.services.app_profiles import match_profiles
from app.services.bulk_discovery import parse_targets


def test_home_assistant_detected_from_supervisor_unit():
    ids = [p.id for p in match_profiles(["hassio-supervisor.service"], [], [])]
    assert "home_assistant" in ids


def test_docker_and_kubernetes_detection():
    assert "docker" in [p.id for p in match_profiles(["docker.service"], [], [])]
    assert "kubernetes_node" in [p.id for p in match_profiles(["kubelet.service"], [], [])]


def test_container_name_detection_is_case_insensitive():
    ids = [p.id for p in match_profiles([], [("HomeAssistant", "ghcr.io/x/y")], [])]
    assert "home_assistant" in ids


def test_no_match_for_plain_host():
    assert match_profiles(["ssh.service", "cron.service"], [], []) == []


def test_parse_targets_expands_ranges_and_cidr():
    assert parse_targets("10.0.0.5") == ["10.0.0.5"]
    assert parse_targets("10.0.0.5-7") == ["10.0.0.5", "10.0.0.6", "10.0.0.7"]
    assert len(parse_targets("10.0.0.0/30")) == 2  # network and broadcast excluded
    assert parse_targets("10.0.0.1, 10.0.0.3") == ["10.0.0.1", "10.0.0.3"]
