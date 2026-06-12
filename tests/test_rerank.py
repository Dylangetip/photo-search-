import numpy as np

from app import classify, config, db, search
from app.main import app
from .test_api import client


def seed_two_rings():
    """Two SKUs whose embeddings are deliberately ambiguous to a query vector."""
    db.upsert_sku("ROUND-RG")
    db.upsert_sku("OVAL-YG")
    v = np.zeros(4, dtype=np.float32)
    a = v.copy(); a[0] = 1.0
    b = v.copy(); b[0] = 0.98; b[1] = np.sqrt(1 - 0.98 ** 2)
    db.add_view("ROUND-RG", "r.png", "single", b)   # slightly worse cosine
    db.add_view("OVAL-YG", "o.png", "single", a)    # slightly better cosine
    db.set_tags("ROUND-RG", {"metal_color": "rose_gold", "center_stone_shape": "round",
                             "setting_type": "solitaire"}, "done")
    db.set_tags("OVAL-YG", {"metal_color": "yellow_gold", "center_stone_shape": "oval",
                            "setting_type": "halo"}, "done")
    q = v.copy(); q[0] = 1.0
    return q


class TestAttributeAgreement:
    def test_full_match(self):
        t = {"metal_color": "rose_gold", "center_stone_shape": "round", "setting_type": "solitaire"}
        assert search.attribute_agreement(t, t) == 1.0

    def test_partial_match(self):
        q = {"metal_color": "rose_gold", "center_stone_shape": "round", "setting_type": "solitaire"}
        c = {"metal_color": "rose_gold", "center_stone_shape": "oval", "setting_type": "solitaire"}
        agree = search.attribute_agreement(q, c)
        assert 0 < agree < 1

    def test_uninformative_fields_skipped(self):
        q = {"metal_color": "other", "center_stone_shape": "unclear", "setting_type": None}
        assert search.attribute_agreement(q, {"metal_color": "rose_gold"}) is None

    def test_empty(self):
        assert search.attribute_agreement({}, {"metal_color": "rose_gold"}) is None


class TestRerank:
    def test_attributes_flip_close_cosine(self):
        """A near-tie on cosine is decided by attribute agreement — the messy-photo case."""
        q = seed_two_rings()
        results = search.search_by_embedding(q, {}, top_k=36)
        assert results[0]["sku"] == "OVAL-YG"  # raw cosine winner

        query_tags = {"metal_color": "rose_gold", "center_stone_shape": "round",
                      "setting_type": "solitaire"}
        reranked = search.rerank_with_query_tags(results, query_tags,
                                                 config.QUERY_RERANK_WEIGHT)
        assert reranked[0]["sku"] == "ROUND-RG"  # attributes flip it
        assert reranked[0]["attr_match"] == 1.0

    def test_untagged_skus_not_penalized(self):
        db.upsert_sku("NOTAGS")
        v = np.zeros(4, dtype=np.float32); v[0] = 1.0
        db.add_view("NOTAGS", "n.png", "single", v)
        results = search.search_by_embedding(v, {}, top_k=36)
        before = results[0]["score"]
        out = search.rerank_with_query_tags(results, {"center_stone_shape": "round"}, 0.35)
        assert out[0]["sku"] == "NOTAGS"
        assert out[0]["score"] == before  # cosine untouched


class TestEndpointWithQueryClassify:
    def test_query_tags_in_response(self, monkeypatch):
        from .test_api import setup_catalog
        setup_catalog()
        monkeypatch.setattr(config, "QUERY_CLASSIFY", True)
        monkeypatch.setattr(config, "CLASSIFY_ENABLED", True)
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(classify, "classify_query_image",
                            lambda img: {"metal_color": "yellow_gold",
                                         "center_stone_shape": "marquise",
                                         "setting_type": "solitaire"})
        import io

        from .test_pipeline import make_beauty
        buf = io.BytesIO(); make_beauty().save(buf, format="PNG")
        with client() as c:
            r = c.post("/api/search/image",
                       files={"file": ("q.png", buf.getvalue(), "image/png")})
        body = r.json()
        assert body["query_tags"]["center_stone_shape"] == "marquise"
        # SKU123 is tagged marquise/yellow_gold/solitaire -> should be boosted to #1
        assert body["results"][0]["sku"] == "SKU123"

    def test_classification_failure_falls_back(self, monkeypatch):
        from .test_api import setup_catalog
        setup_catalog()
        monkeypatch.setattr(config, "QUERY_CLASSIFY", True)
        monkeypatch.setattr(config, "CLASSIFY_ENABLED", True)
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")

        def boom(img):
            raise RuntimeError("API down")
        monkeypatch.setattr(classify, "classify_query_image", boom)
        import io

        from .test_pipeline import make_beauty
        buf = io.BytesIO(); make_beauty().save(buf, format="PNG")
        with client() as c:
            r = c.post("/api/search/image",
                       files={"file": ("q.png", buf.getvalue(), "image/png")})
        assert r.status_code == 200
        body = r.json()
        assert body["query_tags"] is None
        assert len(body["results"]) > 0  # plain CLIP results still returned
