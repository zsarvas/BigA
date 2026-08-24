from __future__ import annotations


def test_connect_wifi_rejects_enterprise(wifi_paths):
    import portal as portal_mod

    ok, msg = portal_mod.connect_wifi("Corp", "pw", security="WPA2 802.1X")
    assert ok is False
    assert "enterprise" in msg.lower()


def test_connect_wifi_requires_password_for_wpa(wifi_paths):
    import portal as portal_mod

    ok, msg = portal_mod.connect_wifi("Home", "", security="WPA2")
    assert ok is False
    assert "password" in msg.lower()


def test_connect_wifi_saves_open_network(wifi_paths):
    import portal as portal_mod
    import wifi_store

    ok, msg = portal_mod.connect_wifi("Office", "", security="--")
    assert ok is True
    nets = wifi_store.load_networks()
    assert nets[0]["ssid"] == "Office"
    assert nets[0]["password"] == ""
    assert nets[0]["security"] == "--"


def test_connect_wifi_saves_wpa_without_touching_nm(wifi_paths, monkeypatch):
    import portal as portal_mod
    import wifi_store

    def boom(*a, **k):
        raise AssertionError("nmcli must not run during portal save")

    monkeypatch.setattr(wifi_store.subprocess, "run", boom)
    ok, _ = portal_mod.connect_wifi("Home", "secret", security="WPA2")
    assert ok is True
    assert wifi_store.load_networks()[0]["password"] == "secret"
