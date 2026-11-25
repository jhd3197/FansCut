"""Sign in with Plex: PIN start, poll, token and server saved."""

from __future__ import annotations

import responses

from fanscut import plextv
from tests.test_api import core_of


def test_pin_flow_saves_token_and_server(client, sonarr_mock):
    sonarr_mock.add(responses.POST, f"{plextv.API}/pins", json={"id": 42, "code": "abcd"})
    sonarr_mock.add(responses.GET, f"{plextv.API}/pins/42", json={"id": 42, "authToken": None})
    start = client.post("/api/plex/pin").json()
    assert start["id"] == 42 and "code=abcd" in start["auth_url"]
    assert client.post("/api/plex/pin/42", json={}).json() == {"done": False}
    sonarr_mock.replace(responses.GET, f"{plextv.API}/pins/42",
                        json={"id": 42, "authToken": "tok-1"})
    sonarr_mock.add(responses.GET, f"{plextv.API}/resources", json=[
        {"name": "Kaito's server", "provides": "server", "owned": True,
         "connections": [{"uri": "https://1-2-3-4.plex.direct:32400", "local": False},
                         {"uri": "http://192.168.1.5:32400", "local": True}]},
        {"name": "phone", "provides": "client", "connections": []}])
    done = client.post("/api/plex/pin/42", json={}).json()
    assert done["done"] and done["url"] == "http://192.168.1.5:32400"
    assert [s["name"] for s in done["servers"]] == ["Kaito's server"]
    cfg = core_of(client).cfg
    assert cfg.library.plex.token == "tok-1" and cfg.library.plex.url == "http://192.168.1.5:32400"


def test_client_id_is_stable(tmp_path):
    assert plextv.client_id(tmp_path) == plextv.client_id(tmp_path)
