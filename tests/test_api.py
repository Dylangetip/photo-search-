import io

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, search, worker
from app.main import app
from .test_pipeline import make_beauty, make_sheet
from .test_worker import drop, ingest_all


def client():
    return TestClient(app)


def setup_catalog():
    drop(make_sheet(), "SKU123_views.jpg")
    drop(make_beauty(), "SKU456_beauty.png")
    ingest_all()
    db.set_tags("SKU123", {"metal_color": "yellow_gold", "center_stone_shape": "marquise",
                           "setting_type": "solitaire", "style_tags": ["vintage"]}, "done")
    db.set_tags("SKU456", {"metal_color": "white_gold", "center_stone_shape": "round",
                           "setting_type": "halo", "style_tags": ["modern"]}, "done")


class TestSearchImage:
    def test_indexed_render_is_top_hit(self):
        """Acceptance #4: searching with an indexed render returns that SKU at #1, sim > 0.95."""
        setup_catalog()
        beauty_view = db.sku_views("SKU456")[0]
        img_path = config.DATA_DIR / beauty_view["file_path"]
        with client() as c:
            r = c.post("/api/search/image",
                       files={"file": ("q.png", img_path.read_bytes(), "image/png")})
        assert r.status_code == 200
        results = r.json()["results"]
        assert results[0]["sku"] == "SKU456"
        assert results[0]["score"] > 0.95
        assert "query_preview" in r.json()

    def test_filters_applied(self):
        setup_catalog()
        beauty_view = db.sku_views("SKU456")[0]
        img_path = config.DATA_DIR / beauty_view["file_path"]
        with client() as c:
            r = c.post("/api/search/image?metal_color=yellow_gold",
                       files={"file": ("q.png", img_path.read_bytes(), "image/png")})
        skus = [x["sku"] for x in r.json()["results"]]
        assert skus and all(s == "SKU123" for s in skus)

    def test_garbage_upload_400(self):
        with client() as c:
            r = c.post("/api/search/image", files={"file": ("x.png", b"not an image", "image/png")})
        assert r.status_code == 400

    def test_query_logged(self):
        setup_catalog()
        buf = io.BytesIO()
        make_beauty().save(buf, format="PNG")
        with client() as c:
            c.post("/api/search/image", files={"file": ("q.png", buf.getvalue(), "image/png")})
        with db.connect() as conn:
            assert conn.execute("SELECT COUNT(*) c FROM query_log").fetchone()["c"] == 1


class TestSearchText:
    def test_keyword_ranking(self):
        """Acceptance #6: tagged SKUs rank above others for matching text."""
        setup_catalog()
        with client() as c:
            r = c.get("/api/search/text", params={"q": "vintage marquise yellow gold"})
        results = r.json()["results"]
        assert results[0]["sku"] == "SKU123"

    def test_hard_filters(self):
        setup_catalog()
        with client() as c:
            r = c.get("/api/search/text",
                      params={"q": "ring", "setting_type": "halo"})
        skus = [x["sku"] for x in r.json()["results"]]
        assert skus == ["SKU456"]


class TestRestApi:
    def test_filters_endpoint(self):
        with client() as c:
            r = c.get("/api/filters")
        body = r.json()
        assert "yellow_gold" in body["metal_color"]
        assert "marquise" in body["center_stone_shape"]

    def test_sku_detail(self):
        setup_catalog()
        with client() as c:
            r = c.get("/api/sku/SKU123")
        body = r.json()
        # detail now returns whole-CAD display images (one per original sheet)
        assert body["image"] and body["image"].endswith(".jpg")
        assert "display/" in body["image"]
        assert len(body["views"]) >= 1
        assert body["tags"]["metal_color"] == "yellow_gold"
        assert client().get("/api/sku/NOPE").status_code == 404

    def test_results_show_whole_cad_display_image(self):
        setup_catalog()
        beauty_view = db.sku_views("SKU456")[0]
        img_path = config.DATA_DIR / beauty_view["file_path"]
        with client() as c:
            r = c.post("/api/search/image",
                       files={"file": ("q.png", img_path.read_bytes(), "image/png")})
        top = r.json()["results"][0]
        assert "display/" in top["image"] and top["image"].endswith(".jpg")
        assert "match_view" in top  # cropped view still available for re-search

    def test_admin_status(self):
        setup_catalog()
        with client() as c:
            r = c.get("/api/admin/status")
        body = r.json()
        assert body["skus_indexed"] == 2
        assert body["views_embedded"] == 5

    def test_pricing_csv(self):
        setup_catalog()
        csv_data = "sku,price,name\nSKU123,3450,Marquise Heirloom\nGHOST,1,x\n"
        with client() as c:
            r = c.post("/api/admin/pricing", files={"file": ("p.csv", csv_data, "text/csv")})
        body = r.json()
        assert body["matched"] == 1
        assert body["unmatched"] == ["GHOST"]
        assert db.get_sku("SKU123")["price"] == 3450.0

    def test_pricing_requires_sku_column(self):
        with client() as c:
            r = c.post("/api/admin/pricing", files={"file": ("p.csv", "a,b\n1,2\n", "text/csv")})
        assert r.status_code == 400

    def test_media_path_traversal_blocked(self):
        with client() as c:
            r = c.get("/media/..%2F..%2Fetc%2Fpasswd")
        assert r.status_code == 404


class TestSearchMath:
    def test_max_cosine_per_sku(self, monkeypatch):
        monkeypatch.setattr(config, "CENTER_EMBEDDINGS", False)  # raw-cosine mechanic
        db.upsert_sku("A")
        db.upsert_sku("B")
        v = np.zeros(4, dtype=np.float32); v[0] = 1
        w = np.zeros(4, dtype=np.float32); w[1] = 1
        db.add_view("A", "a1.png", "single", v)
        db.add_view("A", "a2.png", "single", w)
        db.add_view("B", "b1.png", "single", w)
        results = search.search_by_embedding(v, {})
        assert results[0]["sku"] == "A"
        assert results[0]["score"] == 1.0
        assert results[0]["image"] == "a1.png"  # best view, not first view
