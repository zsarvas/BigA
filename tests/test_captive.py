from __future__ import annotations

def test_looks_like_mac():
    import captive

    assert captive._looks_like_mac("aa:bb:cc:dd:ee:ff")
    assert captive._looks_like_mac("AA:BB:CC:DD:EE:FF")
    assert not captive._looks_like_mac("00:00:00:00:00:00")
    assert not captive._looks_like_mac("not-a-mac")
    assert not captive._looks_like_mac("aa:bb:cc")


def test_wlan_mac_override(monkeypatch):
    import captive

    monkeypatch.setenv("BIGA_WLAN_MAC", "de:ad:be:ef:00:01")
    assert captive.wlan_mac() == "DE:AD:BE:EF:00:01"
    assert captive.ap_ssid_suffix() == "0001"


def test_wlan_interface_override(monkeypatch):
    import captive

    monkeypatch.setenv("BIGA_WLAN_INTERFACE", "wlan1")
    assert captive.wlan_interface() == "wlan1"


def test_ap_ssid_override(monkeypatch):
    import captive

    monkeypatch.setenv("BIGA_AP_SSID", "BigA-TEST")
    assert captive.ap_ssid() == "BigA-TEST"


def test_permanent_mac_from_ethtool(monkeypatch):
    import captive
    import subprocess

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="Permanent address: 11:22:33:44:55:66\n", stderr="")

    monkeypatch.delenv("BIGA_WLAN_MAC", raising=False)
    monkeypatch.setattr(captive.subprocess, "run", fake_run)
    monkeypatch.setattr(captive, "wlan_interface", lambda: "wlan0")
    assert captive.wlan_mac() == "11:22:33:44:55:66"


def test_permanent_mac_rejects_randomized_sysfs(monkeypatch, tmp_path):
    import captive

    iface = tmp_path / "wlan0"
    iface.mkdir()
    (iface / "address").write_text("aa:bb:cc:dd:ee:ff\n")
    (iface / "addr_assign_type").write_text("1\n")  # random

    def boom(*a, **k):
        raise OSError("no ethtool")

    monkeypatch.delenv("BIGA_WLAN_MAC", raising=False)
    monkeypatch.setattr(captive.subprocess, "run", boom)
    monkeypatch.setattr(captive, "_SYS_NET", tmp_path)
    monkeypatch.setattr(captive, "wlan_interface", lambda: "wlan0")
    assert captive.wlan_mac() == ""


def test_is_wifi_interface(tmp_path, monkeypatch):
    import captive

    wlan = tmp_path / "wlan0"
    wlan.mkdir()
    (wlan / "phy80211").mkdir()
    eth = tmp_path / "eth0"
    eth.mkdir()
    bnep = tmp_path / "bnep0"
    bnep.mkdir()
    monkeypatch.setattr(captive, "_SYS_NET", tmp_path)
    assert captive._is_wifi_interface("wlan0")
    assert not captive._is_wifi_interface("eth0")
    assert not captive._is_wifi_interface("bnep0")
