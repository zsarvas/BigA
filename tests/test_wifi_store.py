from __future__ import annotations

import json
import subprocess


def test_load_empty(wifi_paths):
    import wifi_store

    assert wifi_store.load_networks() == []
    assert wifi_store.has_networks() is False


def test_legacy_single_ssid_migrates(wifi_paths):
    import wifi_store

    creds, _ = wifi_paths
    creds.write_text(json.dumps({"ssid": "HomeNet", "password": "secret", "added": 1}))
    nets = wifi_store.load_networks()
    assert len(nets) == 1
    assert nets[0]["ssid"] == "HomeNet"
    assert nets[0]["password"] == "secret"


def test_append_network_newest_first_and_trim(wifi_paths, monkeypatch):
    import wifi_store

    monkeypatch.setattr(wifi_store, "MAX_NETWORKS", 3)
    for i in range(5):
        wifi_store.append_network(f"net{i}", "pw", sync_nm=False)
    nets = wifi_store.load_networks()
    assert [n["ssid"] for n in nets] == ["net4", "net3", "net2"]


def test_append_stores_security_and_exits_provisioning(wifi_paths):
    import wifi_store

    wifi_store.enter_provisioning()
    assert wifi_store.is_provisioning()
    wifi_store.append_network("Office", "", sync_nm=False, security="--")
    assert wifi_store.is_provisioning() is False
    nets = wifi_store.load_networks()
    assert nets[0]["security"] == "--"
    assert nets[0]["password"] == ""


def test_open_and_enterprise_security():
    import wifi_store

    assert wifi_store.is_open_security("")
    assert wifi_store.is_open_security("--")
    assert wifi_store.is_open_security("none")
    assert wifi_store.is_open_security("open")
    assert not wifi_store.is_open_security("WPA2")
    assert wifi_store.network_needs_password("WPA2")
    assert not wifi_store.network_needs_password("--")
    assert not wifi_store.network_needs_password("WPA2 802.1X")
    assert wifi_store.is_enterprise_security("WPA2 802.1X")
    assert wifi_store.is_enterprise_security("WPA-EAP")
    assert not wifi_store.is_enterprise_security("WPA2")


def test_con_name_slug():
    import wifi_store

    assert wifi_store._con_name_for_ssid("Office WiFi") == "biga-wifi-office-wifi"
    assert wifi_store._con_name_for_ssid("!!!") == "biga-wifi-net"


def test_sync_nm_open_network_uses_key_mgmt_none(wifi_paths, monkeypatch):
    import wifi_store

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(wifi_store.subprocess, "run", fake_run)
    monkeypatch.setattr(wifi_store, "list_nm_wifi_profiles", lambda **k: [])
    wifi_store.sync_nm_profiles([{"ssid": "MAC-Allow", "password": "", "security": "--"}])
    add = [c for c in calls if c[:3] == ["nmcli", "connection", "add"]]
    assert add, calls
    assert "wifi-sec.key-mgmt" in add[0]
    i = add[0].index("wifi-sec.key-mgmt")
    assert add[0][i + 1] == "none"
    assert "wpa-psk" not in add[0]


def test_sync_nm_psk_uses_wpa(wifi_paths, monkeypatch):
    import wifi_store

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(wifi_store.subprocess, "run", fake_run)
    monkeypatch.setattr(wifi_store, "list_nm_wifi_profiles", lambda **k: [])
    wifi_store.sync_nm_profiles([{"ssid": "Home", "password": "hunter2", "security": "WPA2"}])
    add = [c for c in calls if c[:3] == ["nmcli", "connection", "add"]][0]
    assert "wpa-psk" in add
    assert "hunter2" in add


def test_sync_nm_skips_empty_password_when_imager_profile_exists(wifi_paths, monkeypatch):
    import wifi_store

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        stdout = ""
        if cmd[:4] == ["nmcli", "-s", "-g", "802-11-wireless.ssid"]:
            stdout = "Office"
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(wifi_store.subprocess, "run", fake_run)
    monkeypatch.setattr(wifi_store, "list_nm_wifi_profiles", lambda **k: ["preconfigured"])
    wifi_store.sync_nm_profiles([{"ssid": "Office", "password": ""}])
    add = [c for c in calls if c[:3] == ["nmcli", "connection", "add"]]
    assert add == []


def test_sync_nm_skips_enterprise(wifi_paths, monkeypatch):
    import wifi_store

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(wifi_store.subprocess, "run", fake_run)
    wifi_store.sync_nm_profiles(
        [{"ssid": "Corp", "password": "x", "security": "WPA2 802.1X"}]
    )
    assert not any(c[:3] == ["nmcli", "connection", "add"] for c in calls)


def test_save_networks_chmod_600(wifi_paths):
    import wifi_store
    import stat

    wifi_store.save_networks([{"ssid": "A", "password": "b"}])
    creds, _ = wifi_paths
    mode = creds.stat().st_mode
    assert stat.S_IMODE(mode) == 0o600
