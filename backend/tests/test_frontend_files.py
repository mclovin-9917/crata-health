from fastapi.testclient import TestClient

from app import main
from app.storage import Storage


def test_index_is_rechecked_and_hashed_assets_are_cached(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<title>Crata Health</title>", encoding="utf-8")
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", tmp_path)
    client = TestClient(main.create_app(Storage()))

    page = client.get("/")
    assert page.status_code == 200 and "Crata Health" in page.text
    assert page.headers["cache-control"] == "no-cache"  # a new build shows up on the next load

    asset = client.get("/assets/index-abc123.js")
    assert "immutable" in asset.headers["cache-control"]
